# Excel → TXT 转换工具

## 项目结构

```
excel2txt/
├── app.py              # Flask 后端
├── requirements.txt    # 依赖列表
├── start.sh            # 一键启动脚本
├── templates/
│   └── index.html      # 前端页面
├── uploads/            # 临时上传目录（自动创建）
└── outputs/            # TXT 输出目录（自动创建）
```

## 启动方式（Git Bash）

```bash
# 进入项目目录
cd /path/to/excel2txt

# 方式一：一键启动
bash start.sh

# 方式二：手动启动
/c/software/Anaconda3/python.exe -m pip install -r requirements.txt
/c/software/Anaconda3/python.exe app.py
```

## 访问

启动后打开浏览器访问：**http://127.0.0.1:5000**

## 功能

- 支持 .xlsx / .xls / .csv 文件上传
- 支持多Sheet批量转换
- 可选列分隔符：Tab / 逗号 / 竖线 / 空格
- 转换结果实时预览（前100行）
- 一键下载 TXT 文件
- 拖拽上传支持
