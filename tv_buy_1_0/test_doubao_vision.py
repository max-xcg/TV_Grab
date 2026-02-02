from llm.doubao_vision import chat_with_images

system_prompt = "你是一个严谨的OCR助手，只输出结果，不要解释。"
user_text = (
    "读取图片中的表格，只输出所有数字（包含小数），"
    "按从上到下、从左到右顺序，用空格分隔。不要输出任何其它文字。"
)

result = chat_with_images(system_prompt, user_text, ["native.png"])

print("识别结果：")
print(result)
