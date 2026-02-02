import os, sys

# 把项目根目录加入 sys.path，保证 g2_lab 可导入
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from g2_lab.services.contrast_ocr_service import (
    contrast_yaml_from_two_images,
    save_contrast_yaml_text,
)

native_img = "native.png"
effective_img = "effective.png"

yaml_text = contrast_yaml_from_two_images(native_img, effective_img)

print("===== 生成的 YAML =====")
print(yaml_text)

out_path = save_contrast_yaml_text(yaml_text)
print("已保存到：", out_path)
