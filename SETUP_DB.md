# 数据库（PostgreSQL）—— pipeline 信息入库

存三类信息，供查询 / 二次筛选 / 去重 / 断点续跑：
- `samples`：每个产出的 (before,after,指令) 一行（含 quality/validity 数值、主体、分片位置、完整 meta JSONB）
- `assets`：资产目录（uid → 来源/类别/名称/许可/标签/被用次数）
- `asset_usage`：已用账本（哪个资产用在哪个样本，跨批次去重）

**架构**：渲染 worker 不碰 DB（只写 `sample.json`）。`orchestrator/ingest.py` 离线读文件入库，
幂等 upsert。DB 挂了不影响渲染。

## 服务器上的库（已建好）

PostgreSQL 16 装在 `root@130.94.66.57`：
- database: `blender_pipeline` / role: `pipeline`（密码见本地 `.env`）
- **对公网开放 5432**（用户选择），已用以下方式加固：
  - 监听 `0.0.0.0`，但 `pg_hba` 只放 `hostssl blender_pipeline pipeline ... scram-sha-256`
    → **必须走 SSL** 且用 `pipeline` 用户 + scram 密码，非 SSL 连接直接拒绝；
  - 密码是 28 位随机强口令。

## 本机直连（已开公网，强制 SSL）

```bash
source .env      # DATABASE_URL=postgresql+psycopg://pipeline:***@130.94.66.57:5432/blender_pipeline?sslmode=require
```

> ⚠️ **安全提醒**：Postgres 直接暴露公网是常见被爆破目标。目前靠 SSL+scram+强密码兜底，
> 但**强烈建议尽快改成 IP 白名单**（把 `0.0.0.0/0` 换成你的客户端/8×H100 机器 IP）：
> ```bash
> HBA=$(ssh root@130.94.66.57 "sudo -u postgres psql -tAc 'show hba_file'")
> ssh root@130.94.66.57 "sudo sed -i 's#0.0.0.0/0#<你的IP>/32#' $HBA && sudo systemctl reload postgresql"
> ```
> 或干脆关掉 5432 防火墙、回到 SSH 隧道（`ssh -f -N -L 5433:localhost:5432 root@130.94.66.57`）。
> 轮换密码：`ssh root@130.94.66.57 "sudo -u postgres psql -c \"ALTER ROLE pipeline PASSWORD '新'\""`。

## 灌数据

```bash
python -m orchestrator.ingest --raw-dir out/smoke_raw --asset-meta assets/objaverse_meta.json
python -m orchestrator.ingest --config configs/default.yaml      # 用 config 的 output_dir
# --db-url 可覆盖；不给则用 $DATABASE_URL，再退回本地 sqlite:///out/pipeline.db
```

## 查询示例

```sql
-- 各算子产出多少
select edit_op, count(*) from samples group by edit_op;
-- 清晰且对齐好的 move 样本（可直接喂训练/人工抽检）
select key, instruction from samples
 where edit_op='object_move' and sharpness>10 and background_diff<1;
-- 某资产用过几次（去重）
select uid, used_count from assets order by used_count desc;
-- 完整 metadata（JSONB，可深查）
select meta->'subject'->>'description' from samples where subject_uid is not null;
```

## 说明
- 本地无 Postgres 也能开发/测试：不设 `DATABASE_URL` 时 `ingest` 落到 `sqlite:///out/pipeline.db`；
  单测（`tests/test_ingest.py`）全走临时 sqlite，不依赖服务器。
- 生产（8×H100）：把 `DATABASE_URL` 指向同一台 Postgres（同机走 localhost，跨机在内网直连或隧道），
  各 job 渲染完由一个 ingest 汇总入库。
