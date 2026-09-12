# -*- coding: utf-8 -*-
"""上传活动数据到 App 实际读取的公共读 bucket。

- bucket tea-wuliao-ocr-ps（华北2 北京，公共读 ACL），与 App kDataBase 一致
- JSON 中 image 字段前缀统一替换为当前 bucket 的公共 URL
- 上传 data/ 与 images/ 目录
- 凭据读取顺序：环境变量 ALIYUN_AK/ALIYUN_SK（GitHub Actions 用 secrets 注入）
  → 回退读取 lib/aliyun_config.dart

本地运行：py deploy_tmp/upload_oss.py [DATA_DIR] [IMG_DIR]
GitHub Actions：python scripts/upload_oss.py . .
"""
import glob
import os
import re
import sys

import oss2

PUBLIC_BUCKET = "tea-wuliao-ocr-ps"
ENDPOINT_HOST = "oss-cn-beijing.aliyuncs.com"
ENDPOINT = f"https://{ENDPOINT_HOST}"
BASE = f"https://{PUBLIC_BUCKET}.{ENDPOINT_HOST}/"


def _load_aliyun_config():
    """凭据读取：环境变量优先，回退 lib/aliyun_config.dart"""
    ak = os.environ.get("ALIYUN_AK", "")
    sk = os.environ.get("ALIYUN_SK", "")
    if ak and sk:
        return ak, sk
    cfg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "lib", "aliyun_config.dart"
    )
    with open(cfg_path, encoding="utf-8") as fp:
        text = fp.read()

    def grab(name):
        m = re.search(rf"{name}\s*=\s*'([^']*)'", text)
        return m.group(1) if m else ""

    return grab("accessKeyId"), grab("accessKeySecret")


AK, SK = _load_aliyun_config()
if not AK or not SK:
    raise SystemExit("未配置 AccessKey（环境变量 ALIYUN_AK/ALIYUN_SK 或 aliyun_config.dart）。")

auth = oss2.Auth(AK, SK)

# 1) 新建公共读 bucket（已存在则复用）
bucket = oss2.Bucket(auth, ENDPOINT, PUBLIC_BUCKET)
try:
    bucket.create_bucket(oss2.BUCKET_ACL_PUBLIC_READ)
    print("created bucket", PUBLIC_BUCKET)
except oss2.exceptions.BucketAlreadyExists:
    print("bucket exists", PUBLIC_BUCKET)
    # 已存在则确保是公共读
    try:
        bucket.put_bucket_acl(oss2.BUCKET_ACL_PUBLIC_READ)
        print("set bucket acl public-read")
    except Exception as e:
        print("set acl failed (may be blocked by public-access guard):", e)

here = os.path.dirname(os.path.abspath(__file__))
# 目录优先取命令行参数（GitHub Actions 传仓库根），否则默认脚本同目录
data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "data")
img_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "images")
data_dir = os.path.abspath(data_dir)
img_dir = os.path.abspath(img_dir)

# 2) 把 JSON 里的图片地址前缀替换为当前 bucket 的公共 URL（幂等）
for f in glob.glob(os.path.join(data_dir, "*.json")):
    with open(f, encoding="utf-8") as fp:
        s = fp.read()
    s2 = re.sub(
        r"https://tea-wuliao-ocr-ps\.oss-cn-beijing\.aliyuncs\.com/", BASE, s
    )
    if s2 != s:
        with open(f, "w", encoding="utf-8") as fp:
            fp.write(s2)
        print("updated", os.path.basename(f))

# 3) 上传 JSON -> data/
for f in glob.glob(os.path.join(data_dir, "*.json")):
    key = "data/" + os.path.basename(f)
    bucket.put_object_from_file(
        key, f, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    print("uploaded", key)

# 4) 上传海报 -> images/
for f in glob.glob(os.path.join(img_dir, "*.jpg")):
    key = "images/" + os.path.basename(f)
    bucket.put_object_from_file(
        key, f, headers={"Content-Type": "image/jpeg"}
    )
    print("uploaded", key)

print("DONE")
