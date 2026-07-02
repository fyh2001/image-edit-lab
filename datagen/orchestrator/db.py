"""
数据库层：SQLAlchemy 模型 + 引擎/会话工具。存 pipeline 的三类信息：

- samples     ：每个产出的 (before,after,指令) 一行（含 quality/validity 分数、主体、分片位置、完整 meta）
- assets      ：资产目录（uid → 来源/类别/名称/许可/尺寸/被用次数）
- asset_usage ：已用账本（哪个资产用在了哪个样本/job，跨批次去重）

连接串走 `DATABASE_URL` 或 --db-url；缺省回退本地 sqlite（便于单测/离线）。
worker 不碰 DB —— 由 orchestrator/ingest.py 读 sample.json 灌库，解耦渲染与存储。
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (String, Integer, Float, Boolean, DateTime, JSON,
                        UniqueConstraint, create_engine)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# Postgres 用 JSONB（可查询），其它（sqlite）退回普通 JSON
_JSON = JSON().with_variant(JSONB, "postgresql")


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Sample(Base):
    __tablename__ = "samples"
    key: Mapped[str] = mapped_column(String, primary_key=True)          # job_id_v{view}
    job_id: Mapped[str] = mapped_column(String, index=True)
    view: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scene_name: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    scene_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # 解析后的真实场景 id（溯源）
    source_dataset: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    pipeline_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    edit_op: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    instruction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subject_category: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    subject_uid: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    subject_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 质量/有效性数值（便于直接 SQL 过滤，不用解 JSON）
    change_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpness: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    background_diff: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    penetration_depth: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    floating_gap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reseated: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    collision_free: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    num_attempts: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    direction_consistent: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    shard_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # collector 回填
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    meta: Mapped[dict] = mapped_column(_JSON)                          # 完整 sample.json


class Asset(Base):
    __tablename__ = "assets"
    uid: Mapped[str] = mapped_column(String, primary_key=True)
    source: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)  # objaverse/hssd/...
    category: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    license: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tags: Mapped[Optional[list]] = mapped_column(_JSON, nullable=True)
    target_size: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AssetUsage(Base):
    __tablename__ = "asset_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_uid: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    sample_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    __table_args__ = (UniqueConstraint("asset_uid", "sample_key", name="uq_asset_sample"),)


def get_engine(url: Optional[str] = None):
    url = url or os.environ.get("DATABASE_URL") or "sqlite:///out/pipeline.db"
    return create_engine(url, future=True)


def init_db(engine):
    Base.metadata.create_all(engine)
    _migrate_add_columns(engine)


def _migrate_add_columns(engine):
    """给已存在的 samples 表补新列（老库无缝升级；create_all 不会 ALTER 现有表）。"""
    from sqlalchemy import text
    for name in ("scene_id", "source_dataset", "pipeline_version"):
        try:                                   # PostgreSQL：幂等
            with engine.begin() as c:
                c.execute(text(f"ALTER TABLE samples ADD COLUMN IF NOT EXISTS {name} VARCHAR"))
        except Exception:
            try:                               # SQLite：无 IF NOT EXISTS，已存在则忽略
                with engine.begin() as c:
                    c.execute(text(f"ALTER TABLE samples ADD COLUMN {name} VARCHAR"))
            except Exception:
                pass


def make_session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
