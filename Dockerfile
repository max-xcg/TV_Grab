FROM python:3.11-slim

WORKDIR /app

# 先装依赖（利用缓存）
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 再拷贝代码
COPY . /app

# 确保 start.sh 可执行
RUN chmod +x /app/start.sh

ENV PORT=8000
EXPOSE 8000

CMD ["/app/start.sh"]
