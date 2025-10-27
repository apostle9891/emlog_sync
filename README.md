# Emlog 博客同步工具

自动将 Git 仓库中的 Markdown 文件同步到 Emlog 博客。

## 核心功能

- ✅ 自动检测文件变更（新增/修改/删除）
- ✅ 支持 Markdown Front Matter 元数据
- ✅ 自动上传本地图片到 Emlog
- ✅ 智能分类匹配：自动匹配已有分类
- ✅ 智能缓存，避免重复同步
- ✅ 失败自动重试机制
- ✅ 支持文件忽略规则
- ✅ 完整的初始化支持

## 工作原理

```
Git Push → Git Hook → checkout -f 更新文件
                            ↓
                    执行同步脚本 (sync.py)
                            ↓
                    对比文件系统 vs 缓存
                            ↓
                    根据状态调用 Emlog API
                            ↓
                    更新缓存，同步完成
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

依赖包：
- `requests` - HTTP 请求
- `pyyaml` - 配置文件解析

### 2. 配置文件

编辑 `config.yaml`，填写你的 Emlog 配置：

```yaml
emlog:
  base_url: "https://yourdomain.com"  # 你的博客地址
  api_key: "your_api_key_here"         # Emlog API Key
  author_uid: 1                         # 作者 ID
  default_category_id: 1                # 默认分类 ID（无分类时使用）

git:
  work_tree: "/var/www/hexo"            # Git 工作目录
  git_dir: "/var/repo/blog.git"         # Git 仓库目录
```

**重要配置说明：**
- `api_key`：在 Emlog 后台 → 系统设置 → API 设置 中获取
- `default_category_id`：当文章没有指定分类时使用的默认分类ID（通常为 1）

### 3. 首次初始化

**方式一：安全模式（推荐）**

```bash
# 修改 config.yaml
init:
  mode: "safe"  # 仅扫描建立缓存，不立即同步

# 运行初始化
python3 sync.py

# 检查生成的 .emlog_cache.json，确认无误后再次运行开始同步
python3 sync.py
```

**方式二：全量同步**

```bash
# 修改 config.yaml
init:
  mode: "sync"  # 立即发布所有文章

# 运行初始化（会立即同步所有文件到 Emlog）
python3 sync.py
```

### 4. Git Hook 集成

编辑 Git 仓库的 `hooks/post-receive`：

```bash
#!/bin/bash

# 检出最新代码
git --work-tree=/var/www/hexo --git-dir=/var/repo/blog.git checkout -f

# 运行同步脚本
cd /path/to/emlog-sync
python3 sync.py

# 记录结果
if [ $? -eq 0 ]; then
    echo "✓ Emlog sync completed successfully"
else
    echo "✗ Emlog sync failed, check logs"
    exit 1
fi
```

赋予执行权限：

```bash
chmod +x hooks/post-receive
```

### 5. 开始同步

```bash
# 手动运行同步
python3 sync.py

# 或通过 Git push 触发（配置 Hook 后）
git push origin main
```

**查看日志：**

```bash
# 实时查看日志
tail -f emlog_sync.log

# 检查是否有错误
grep ERROR emlog_sync.log
```

## Markdown Front Matter

在 Markdown 文件开头添加 Front Matter：

```yaml
---
title: 文章标题
date: 2025-10-25 12:00
tags:
  - 标签1
  - 标签2
categories:
  - 分类名称
cover: /images/cover.png
published: "true"
sticky: "0"
comments: true
---

文章正文内容...
```

**字段说明：**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `title` | 文章标题 | 必填 |
| `date` | 发布时间 | 当前时间 |
| `tags` | 标签（列表） | 无 |
| `categories` | 分类（取最后一个） | 默认分类 |
| `cover` | 封面图 | 无 |
| `published` | 是否发布（true/false） | true |
| `sticky` | 是否置顶（0/1） | 0 |
| `comments` | 是否允许评论 | true |

## 文件状态

Map 缓存（`.emlog_cache.json`）中记录了每个文件的状态：

| 状态 | 说明 | 操作 |
|------|------|------|
| `unsynced` | 新文件，未同步 | 发布新文章 |
| `synced` | 已同步，内容一致 | 跳过 |
| `modified` | 已修改，待更新 | 更新文章 |
| `deleted` | 已删除，待同步删除 | 删除文章 |
| `failed` | 同步失败 | 重试或人工处理 |
| `ignored` | 忽略文件 | 跳过 |

## 文件忽略

在 `config.yaml` 中配置忽略规则：

```yaml
ignore_patterns:
  - "README.md"
  - "draft/*"      # 忽略 draft 目录
  - "*.draft.md"   # 忽略草稿文件
  - "_*"           # 忽略以下划线开头的文件
```

## 常见问题

### 1. API 鉴权失败

检查 `config.yaml` 中的 `api_key` 是否正确，确保 Emlog 后台已开启 API。

### 2. 图片上传失败

图片路径应相对于 `work_tree` 目录：

**正确示例：**
```markdown
![图片](images/example.png)          # 对应 work_tree/images/example.png
![图片](/images/example.png)         # 同上（开头的/会被自动处理）
```

**注意事项：**
- 图片路径支持URL编码（如 %20 会自动解码为空格）
- 确保图片文件存在于 work_tree 目录下
- 支持的格式：jpg, jpeg, png, gif, webp 等

### 3. 分类处理逻辑

**智能分类匹配：**

1. **无分类**：如果文章 Front Matter 中未指定 `categories`，自动使用默认分类
2. **分类已存在**：直接使用 Emlog 中已有的分类
3. **分类不存在**：使用默认分类，并记录警告日志

**示例：**

```yaml
---
title: 我的文章
categories:
  - 技术博客
  - Python教程
---
```

系统会：
1. 取最后一个分类 "Python教程" 作为文章分类
2. 检查 Emlog 是否有 "Python教程" 分类
3. 如果有，使用该分类
4. 如果没有，使用默认分类并记录警告

**建议：** 在 Emlog 后台预先创建好需要的分类，避免文章都发布到默认分类。

### 4. 同步失败重试

系统会自动重试 3 次（可在 `config.yaml` 中配置 `max_retries`），超过次数需要人工检查日志并修复。

### 5. 手动修复缓存

如果缓存文件损坏，可以：

```bash
# 备份现有缓存
mv .emlog_cache.json .emlog_cache.json.bak

# 删除缓存重新初始化
rm .emlog_cache.json
python3 sync.py
```

## 日志查看

```bash
# 查看实时日志
tail -f emlog_sync.log

# 查看最近的同步记录
tail -n 50 emlog_sync.log
```

## 目录结构

```
emlog-sync/
├── sync.py              # 主程序
├── config.yaml          # 配置文件
├── requirements.txt     # Python 依赖
├── README.md            # 使用文档
├── doc/
│   └── emlog.md        # 详细技术文档
├── .emlog_cache.json    # Map 缓存（自动生成）
└── emlog_sync.log       # 同步日志（自动生成）
```

## 系统要求

- Python >= 3.8
- Emlog Pro 版本
- Git 仓库环境

## 注意事项

1. **首次运行建议使用安全模式**，先检查缓存文件确认无误
2. **备份重要数据**，避免误操作
3. **定期检查日志**，及时发现问题
4. **测试环境先试用**，确认无误后再用于生产环境
5. **API Key 保密**，不要提交到公开仓库

## 进阶使用

### 手动触发同步

```bash
python3 sync.py
```

### 查看缓存状态

```bash
cat .emlog_cache.json | jq '.files | to_entries[] | select(.value.status != "synced")'
```

### 手动修改文章状态

编辑 `.emlog_cache.json`，修改对应文件的 `status` 字段：

```json
{
  "files": {
    "posts/example.md": {
      "status": "unsynced",  // 改为 unsynced 会重新同步
      "emlog_id": 123,
      ...
    }
  }
}
```

## 技术支持

- [Emlog API 官方文档](https://www.emlog.net/docs/api/)
- Issues: GitHub Issues
- 详细文档：查看 `doc/emlog.md`

## 许可证

MIT License

---

**版本：** v1.0  
**最后更新：** 2025-10-25

