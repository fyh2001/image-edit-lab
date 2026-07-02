"""
集中 import 所有插件模块，触发它们的 @register_* 装饰器执行。
新增插件后，在这里加一行 import 即可被系统发现。
"""
# 资产
from datagen.worker.assets import objaverse_provider   # noqa: F401
from datagen.worker.assets import haven_provider        # noqa: F401
from datagen.worker.assets import primitives_provider   # noqa: F401  （冒烟测试用，零下载）
from datagen.worker.assets import composite_provider     # noqa: F401  多数据集组合（objaverse+primitives...）
# 场景
from datagen.worker.scene import tabletop               # noqa: F401
from datagen.worker.scene import room                   # noqa: F401  合成房间（验证 ceiling/wall/floating）
from datagen.worker.scene import hssd                   # noqa: F401  HSSD 真实室内场景
from datagen.worker.scene import front3d                # noqa: F401
# 编辑算子
from datagen.worker.edits import transform_edits        # noqa: F401
from datagen.worker.edits import presence_edits         # noqa: F401
# 渲染后端
from datagen.worker.render import backends              # noqa: F401
