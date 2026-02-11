# TV_Grab 目录文件说明（基于实际扫描结果）

> 本文档**严格基于以下命令的真实输出编写**：
>
> ```bash
> cd ~/tvlabs_scraper/TVLabs/TV_Grab
> find . -type f > _file_list_all.txt
> ```
>
> 不做假设、不引入不存在的文件，仅解释**当前仓库内真实存在的文件与目录**。

------

## 一、工程整体用途

**TV_Grab** 是一个以「电视型号结构化数据（YAML）」为核心的综合工程，覆盖：

- 📦 电视规格数据抓取、规范化、归档
- 🧠 电视选购推荐（搜索 / 排序 / 对比 / 解释）
- 🧪 G2 Lab 画质测试、OCR、分析与报告生成
- 🤖 为 Bot / Agent（如 Clawdbot）提供可调用的 CLI 工具

------

## 二、Git / 工程元数据（版本管理）

### `.git/` 及其子文件

- **作用**：Git 版本控制内部数据
- **是否参与业务**：❌ 不参与
- **说明**：包括提交记录、对象存储、hooks 等

### `.gitignore`

- **作用**：定义 Git 忽略规则
- **是否参与业务**：❌

### `.gitattributes`

- **作用**：Git 属性配置（如换行符、LFS）
- **是否参与业务**：❌

------

## 三、环境与工程配置

### `.env`

- **作用**：环境变量配置（API Key、路径等）
- **使用位置**：LLM / OCR / 外部 API
- **是否必须**：⚠️ 取决于是否调用外部服务

### `Dockerfile`

- **作用**：Docker 容器构建文件
- **当前阶段**：❌ 不参与 Clawdbot MVP

------

## 四、品牌与数据规范

### `brands.yaml`

- **作用**：品牌名称、路径、映射定义
- **使用模块**：爬虫 / 数据整理 / 选购逻辑
- **重要性**：✅ 高

### `convert_to_spec_yaml.py`

- **作用**：将原始数据转换为标准 spec YAML
- **使用阶段**：数据生产阶段
- **Clawdbot**：❌ 不直接使用

------

## 五、原始数据与图片资产

### `data_raw/uploads/*.png`

- **作用**：上传的测试图片 / 截图
- **使用模块**：OCR / G2 Lab 测试
- **Clawdbot**：❌ 不使用

### `effective.png` / `native.png`

- **作用**：示例或测试图片
- **Clawdbot**：❌

------

## 六、电视规格数据（核心数据层）

### `output_all_brands_2026_spec/`

示例文件：

- `hisense_海信e8s_75英寸_spec.yaml`
- `hisense_海信e8s_85英寸_spec.yaml`

**作用**：

- 存放已规范化的电视规格 YAML（按品牌 / 年份）

**重要性**：

- ⭐⭐⭐⭐⭐（选购服务的核心数据来源）

------

## 七、tv_buy_1_0 —— 电视选购与评测主模块

### 1️⃣ `tv_buy_1_0/tools_cli/`（⭐ Clawdbot 核心）

该目录下每个文件都是一个**命令行工具（CLI）**，特点：

- 参数通过 argparse 传入
- 结果通过 stdout 输出（JSON）
- 只读数据，不修改 YAML

#### 文件说明

- `tv_search.py`
  - 按尺寸 / 预算 / 品牌进行初筛
- `tv_rank.py`
  - 按场景权重（电影 / 体育 / 游戏等）进行评分排序
- `tv_compare.py`
  - 多型号参数对比
- `fill_price_cny_from_api.py`
  - 价格数据回填工具（数据维护用）

------

### 2️⃣ 推荐与解释逻辑

- `run_reco.py`
  - 推荐流程总控
- `reasons_v2.py`
  - 场景化推荐理由生成

------

### 3️⃣ 数据与 Schema

- `schema/core_schema.yaml`
- `schema/field_mapping.yaml`

用于定义字段规范与映射关系。

------

### 4️⃣ G2 Lab 测试与分析模块（非 Clawdbot 核心）

#### `g2_lab/`

包含：

- OCR 抽取（`services/`）
- 分析与报告生成（`report/`）
- FastAPI 路由（`api/`）

主要用于画质测试与评测报告生成。

------

### 5️⃣ LLM 相关模块

#### `llm/`

- `deepseek_client.py`
- `doubao_vision.py`
- `deepseek_vision.py`

**作用**：

- 调用不同大模型完成文本 / 视觉分析

------

### 6️⃣ Web 服务

#### `web/`

- `app.py`
- `templates/index.html`

FastAPI Web Demo，用于测试或展示。

------

## 八、测试、工具与中间产物

### `tools/`

- 临时分析 / 数据处理工具

### `test_*.py`

- 测试脚本

### `summaries/contrast_records/`

- 已生成的分析记录与结果

------

## 九、工程自检文件

### `_file_list_all.txt`

- **作用**：完整文件扫描结果
- **用途**：工程审计 / 文档生成依据

### `_reports/*.csv`

- 数据处理过程中的统计结果

------

## 十、结论（基于当前目录状态）

- ✅ 电视规格 YAML：齐全
- ✅ 选购 CLI 工具：已存在（`tools_cli/`）
- ✅ 推荐与解释逻辑：完整
- ⚠️ Clawdbot 接入仅需在现有 CLI 基础上做配置

------

**文档说明**：

- 本文件与 `_file_list_all.txt` 一一对应
- 不包含任何“规划中但不存在”的文件
- 用于当前阶段工程理解与后续 Bot 接入