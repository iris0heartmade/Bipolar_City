# BIPOLE 初版框架与页面实施计划

**目标：** 本地可运行的等距城市游戏，支持住宅建造、施工、迁入、财政和保存恢复。

**架构：** Python/FastAPI 持有唯一世界状态；原生 JavaScript/Canvas 绘制与交互。配置使用 JSON，存档使用版本化 JSON。单一模拟服务串行处理命令，服务端时钟负责自动运行。

**技术：** Python 3.11+、FastAPI、Uvicorn、pytest、原生 ES modules、Canvas 2D。

本次按用户明确要求直接在当前会话实施，不创建子代理或发布云端网站。

## 页面设计

暖白纸张、深绿导航、黄铜点缀；已有河岸城镇置于中央等距画布。左侧选择建筑，顶部财政与人口，右侧城市概况/建筑详情/月报，底部暂停与速度。小屏折叠侧栏，保留地图与建造操作。所有图像为程序绘制或本地 SVG，无远程素材依赖。

## 文件与执行步骤

- [x] 建立 `backend/model`、`backend/core`、`backend/systems`、`backend/persistence`、`backend/api`、`frontend/js`、`frontend/css`、`frontend/assets`、`data/templates`、`tests`、`saves` 目录。
- [x] 编写 `data/buildings.json`、`data/balance.json`、`data/templates/river_town.json`；所有数值注明测试参数。
- [x] 在 `backend/model/world.py` 定义状态；`systems/placement.py` 处理旋转、地形、占地与路网；`generator.py` 按种子生成已有城镇。
- [x] 在 `construction.py` 实现蓝图、批次确认、施工与完成；`population.py` 实现容量约束；`finance.py` 实现收入和支出账本；`core/simulation.py` 固定步长调度。
- [x] 在 `persistence/savegame.py` 校验版本与完整状态，原子存储并备份；`core/service.py` 串行提交命令；`api/routes.py` 提供状态与操作接口。
- [x] 在 `frontend/js/art.js` 绘制所有建筑和自然素材；`renderer.js` 处理等距画布；`controls.js` 处理选格、拖动、缩放、旋转；`ui.js` 更新中文界面；`api.js` 处理请求。
- [x] `python -m pytest -q`：验证生成、放置、重复收费、暂停、容量、120 月及存档连续性。
- [x] `python -m backend.app` 启动；浏览器实际建造住宅、确认施工、推进时间、核对人口和财政、保存恢复；记录结果到 `docs/验收记录.md`。
- [x] 更新 `README.md`、中文 `CHANGELOG.md` 和任务进度，交付本地预览与限制说明。

## 初版边界

测试城名与数值不属于小说正史。暂实现住宅、公共绿地与基础建筑目录；商业与生产设施只提供简化就业/维护，不实现商户自主经营。没有银行、科技树、议会、NPC 城市深度模拟；不展示虚假的可用入口。住房销售和按揭未实现，初版新建住房采用市政持有。已完成建筑暂不支持拆除，防止不完整产权与退款机制产生套利。
