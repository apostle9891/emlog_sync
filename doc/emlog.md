# Emlog 博客同步实现文档

## 概述

本系统实现 Git 仓库与 Emlog 博客的自动同步功能。当 Markdown 文件通过 Git 提交后，系统自动检测变更并同步到 Emlog 博客平台。

**部署场景：**
```bash
# Git hooks/post-receive 脚本触发
git --work-tree=/var/www/hexo --git-dir=/var/repo/blog.git checkout -f
# 然后执行本同步程序
```

**核心功能：**
- **本地缓存映射管理**：维护 Git 仓库所有 Markdown 文件的状态和同步信息
- 检测 Git 仓库中 Markdown 文件的变更（新增/修改/删除）
- 解析 Markdown Front Matter 元数据
- 处理本地图片上传到 Emlog
- 调用 Emlog API 发布/更新/删除文章
- 使用 AI 生成文章摘要

---

## 1. 系统架构

### 1.1 整体流程

```
Git Push → Git Hook → checkout -f 更新文件 → 执行同步脚本
                                                    ↓
                                          加载/创建 Map 缓存
                                                    ↓
                                        对比文件系统 vs 缓存
                                        （扫描工作目录所有.md文件）
                                                    ↓
                                            更新文件状态
                                    （新文件/已同步/已修改/已删除）
                                                    ↓
                                        根据状态调用 Emlog API
                                    （发布/更新/删除/跳过/重试）
                                                    ↓
                                            更新 Map 缓存
                                                    ↓
                                              同步完成
```

### 1.2 核心模块

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| **Map缓存管理器** | 维护所有文件状态和映射关系 | 文件路径 | 文件状态、Emlog ID、哈希值 |
| **文件系统扫描器** | 扫描工作目录所有Markdown文件 | 工作目录路径 | .md 文件列表 |
| **状态对比器** | 对比文件系统与缓存，确定状态 | 文件列表 + Map缓存 | 文件状态（新增/修改/删除/已同步）|
| **文件解析器** | 解析 Markdown 内容 | .md 文件 | Front Matter + 正文 |
| **资源处理器** | 上传本地图片 | 本地图片路径 | Emlog 图片URL |
| **AI摘要生成器** | 生成文章摘要 | 正文内容 | 摘要文本(100字) |
| **API客户端** | 调用 Emlog API | 文章数据 | API响应 |
| **状态同步器** | 根据状态执行同步操作 | 文件状态 | 同步结果并更新缓存 |

---

## 2. 数据结构

### 2.1 本地缓存映射文件格式 (`.emlog_cache.json`)

**核心设计思想：** 维护 Git 仓库中所有 Markdown 文件的完整状态信息

```json
{
  "version": "1.0",
  "last_sync": "2025-10-25T12:30:00",
  "files": {
    "posts/ai-tutorial.md": {
      "status": "synced",
      "emlog_id": 123,
      "md5_hash": "a1b2c3d4e5f6...",
      "last_modified": "2025-10-20T10:00:00",
      "sync_time": "2025-10-20T10:05:00",
      "retry_count": 0,
      "error_message": null
    },
    "posts/tech-blog.md": {
      "status": "modified",
      "emlog_id": 124,
      "md5_hash": "f6e5d4c3b2a1...",
      "last_modified": "2025-10-25T09:00:00",
      "sync_time": "2025-10-24T08:00:00",
      "retry_count": 0,
      "error_message": null
    },
    "posts/new-article.md": {
      "status": "unsynced",
      "emlog_id": null,
      "md5_hash": "1234567890ab...",
      "last_modified": "2025-10-25T11:00:00",
      "sync_time": null,
      "retry_count": 0,
      "error_message": null
    }
  }
}
```

### 2.2 文件状态定义

| 状态 | 说明 | 判断条件 | 下一步操作 |
|------|------|----------|-----------|
| `unsynced` | 新文件，未同步 | 文件系统存在，但缓存中无记录 | 调用 `article_post` 发布新文章 |
| `synced` | 已同步，内容一致 | 文件存在，缓存有记录，hash值相同 | 无操作（跳过） |
| `modified` | 已修改，待更新 | 文件存在，缓存有记录，hash值不同 | 调用 `article_draft_edit` 更新文章 |
| `deleted` | 已删除，待同步删除 | 文件系统不存在，但缓存中有记录 | 调用 `article_del` 删除文章，然后从缓存移除 |
| `failed` | 同步失败 | API调用失败（可重试） | 根据retry_count决定重试或人工介入 |
| `ignored` | 忽略文件 | 符合忽略规则（配置文件指定） | 永久跳过，不处理 |

### 2.3 状态转换图

```
                 文件系统对比缓存
                        │
        ┌───────────────┼───────────────┐
        │               │               │
   文件系统有       文件系统有       文件系统无
   缓存无           缓存有           缓存有
        │               │               │
        ↓               ↓               ↓
    unsynced    ┌───────┴───────┐   deleted
        │       │               │       │
        │   hash相同         hash不同   │
        │       │               │       │
        │       ↓               ↓       │
        │    synced         modified    │
        │    (跳过)            │        │
        │                      │        │
        └──────┬───────────────┘        │
               │                        │
            调用API                  调用API
        article_post           article_draft_edit     article_del
               │                        │                 │
         ┌─────┴─────┐           ┌─────┴─────┐          │
         │           │           │           │          │
       成功        失败        成功        失败        成功
         │           │           │           │          │
         ↓           ↓           ↓           ↓          ↓
      synced      failed      synced      failed   从缓存移除
                     │                       │
                     └───────┬───────────────┘
                             │
                      retry_count < 最大值？
                             │
                   ┌─────────┴─────────┐
                  是                   否
                   │                    │
                重新尝试            人工介入
                   │                (记录错误)
            返回对应状态流程
```

### 2.4 Markdown Front Matter 格式

```yaml
---
title: github大模型软件评测          # 必填
date: 2025-02-19 00:27              # 可选，默认当前时间
updated:                             # 可选
tags:                                # 可选
  - 大模型
  - AI评测
categories:                          # 可选
  - 大模型专区
  - 大模型评测
series: 系列文章                      # 忽略（Emlog不支持）
comments:                            # 可选，空值表示允许评论
cover: /images/github.png            # 可选
published: "true"                    # 可选，默认true
sticky: "1"                          # 可选，1=置顶
---
```

### 2.5 Front Matter 到 Emlog API 字段映射

| Markdown 字段 | Emlog API 字段 | 类型 | 说明 |
|--------------|----------------|------|------|
| `title` | `title` | string | 文章标题 |
| `tags` | `tag` | string | 逗号分隔，如："标签1,标签2" |
| `categories` | `sort_id` | int | **需要先查询分类ID** |
| `cover` | `cover` | string | 封面图URL（需要先上传）|
| `sticky` | `top` | string | "y"=置顶, "n"=不置顶 |
| `date` | `post_date` | int | Unix时间戳 |
| `published` | `draft` | string | "n"=已发布, "y"=草稿 |
| `comments` | `allow_remark` | string | "y"=允许, "n"=不允许 |
| - | `excerpt` | string | **AI生成**，100字摘要 |
| - | `content` | string | Markdown正文 |
| - | `author_uid` | int | 作者用户ID（配置项）|

---

## 3. 首次初始化流程

### 3.1 初始化场景

当首次运行同步程序时（`.emlog_cache.json` 文件不存在），需要根据工作目录的实际情况进行初始化：

| 场景 | 工作目录状态 | Emlog博客状态 | 处理策略 |
|------|------------|--------------|----------|
| **场景1** | 已有多篇Markdown文件 | 空博客 | 扫描所有文件，标记为 `unsynced`，全量同步到Emlog |
| **场景2** | 已有多篇Markdown文件 | 已有部分文章 | 智能匹配模式：尝试建立映射关系 |
| **场景3** | 空目录（没有.md文件） | 任意状态 | 创建空缓存文件，等待后续文件添加 |

### 3.2 初始化配置选项

在 `config.yaml` 中添加初始化行为配置：

```yaml
# 初始化配置
init:
  # 首次运行的行为模式
  mode: "safe"  # safe（仅建立缓存，不同步） | sync（全量同步） | match（智能匹配）
  
  # 智能匹配规则（mode=match时有效）
  match_by: "title"  # title（按标题匹配） | date（按日期匹配） | title_and_date
  
  # 是否扫描Emlog已有文章
  scan_existing: true
  
  # 已有文章的处理方式
  existing_behavior: "skip"  # skip（跳过） | update（更新） | create_new（创建新文章）
```

### 3.3 初始化流程详细步骤

```python
def initialize_cache():
    """
    首次初始化缓存映射文件
    扫描工作目录的所有Markdown文件并建立缓存
    """
    print("=" * 60)
    print("检测到首次运行，开始初始化...")
    print("=" * 60)
    
    # 1. 扫描工作目录中的所有Markdown文件
    work_tree = config['git']['work_tree']
    all_md_files = scan_markdown_files(work_tree)
    print(f"发现 {len(all_md_files)} 个Markdown文件")
    
    if len(all_md_files) == 0:
        # 空目录，创建空缓存
        print("工作目录为空，创建空缓存文件")
        cache = create_empty_cache()
        save_cache(cache)
        return cache
    
    # 2. 根据配置决定初始化模式
    init_mode = config['init']['mode']
    print(f"初始化模式: {init_mode}")
    
    if init_mode == 'safe':
        # 安全模式：仅建立缓存，标记为 unsynced，不立即同步
        cache = initialize_safe_mode(all_md_files, work_tree)
        
    elif init_mode == 'sync':
        # 同步模式：立即全量同步到Emlog
        cache = initialize_sync_mode(all_md_files, work_tree)
        
    elif init_mode == 'match':
        # 匹配模式：智能匹配Emlog已有文章
        cache = initialize_match_mode(all_md_files, work_tree)
    
    # 3. 保存缓存文件
    save_cache(cache)
    print("=" * 60)
    print("初始化完成！")
    print("=" * 60)
    return cache

def scan_markdown_files(work_tree):
    """
    扫描工作目录中的所有Markdown文件
    返回相对路径列表
    """
    import os
    md_files = []
    
    for root, dirs, files in os.walk(work_tree):
        # 排除.git目录
        if '.git' in dirs:
            dirs.remove('.git')
        
        for file in files:
            if file.endswith('.md'):
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, work_tree)
                md_files.append(rel_path)
    
    return md_files
```

### 3.4 三种初始化模式详解

#### 模式1：安全模式（推荐首次使用）

**特点：** 不会对Emlog进行任何修改，仅扫描文件并建立本地缓存，标记为 `unsynced`

**适用场景：** 首次部署，希望先检查哪些文件会被同步

```python
def initialize_safe_mode(md_files, work_tree):
    """
    安全模式：扫描所有文件，标记为 unsynced，但不立即同步
    """
    import os
    from datetime import datetime
    
    cache = create_empty_cache()
    
    for file_path in md_files:
        full_path = os.path.join(work_tree, file_path)
        
        # 检查是否应该忽略
        if should_ignore_file(file_path):
            cache['files'][file_path] = {
                "status": "ignored",
                "emlog_id": None,
                "md5_hash": calculate_md5(full_path),
                "last_modified": get_file_mtime(full_path),
                "sync_time": None,
                "retry_count": 0,
                "error_message": None
            }
            print(f"  [忽略] {file_path}")
        else:
            cache['files'][file_path] = {
                "status": "unsynced",
                "emlog_id": None,
                "md5_hash": calculate_md5(full_path),
                "last_modified": get_file_mtime(full_path),
                "sync_time": None,
                "retry_count": 0,
                "error_message": None
            }
            print(f"  [未同步] {file_path}")
    
    print(f"\n共扫描 {len(md_files)} 个文件")
    print("提示：")
    print("  - 文件已标记为 unsynced 状态")
    print("  - 下次运行将自动同步这些文件到Emlog")
    print("  - 如不想同步某些文件，请手动修改 .emlog_cache.json 将其状态改为 ignored")
    
    return cache
```

#### 模式2：全量同步模式

**特点：** 立即将所有文件同步到Emlog

**适用场景：** Emlog是空博客，需要一次性发布所有文章

```python
def initialize_sync_mode(md_files, work_tree):
    """
    全量同步模式：扫描所有文件并立即发布到Emlog
    """
    import os
    from datetime import datetime
    
    cache = create_empty_cache()
    api = EmlogAPI(config)
    categories = api.get_categories()
    
    success_count = 0
    failed_count = 0
    
    for file_path in md_files:
        full_path = os.path.join(work_tree, file_path)
        
        # 检查是否应该忽略
        if should_ignore_file(file_path):
            cache['files'][file_path] = {
                "status": "ignored",
                "emlog_id": None,
                "md5_hash": calculate_md5(full_path),
                "last_modified": get_file_mtime(full_path),
                "sync_time": None,
                "retry_count": 0,
                "error_message": None
            }
            print(f"  [忽略] {file_path}")
            continue
        
        print(f"\n正在同步: {file_path}")
        
        try:
            # 发布文章
            article_id = publish_new_article(full_path, api, categories)
            
            # 记录到缓存（已同步状态）
            cache['files'][file_path] = {
                "status": "synced",
                "emlog_id": article_id,
                "md5_hash": calculate_md5(full_path),
                "last_modified": get_file_mtime(full_path),
                "sync_time": datetime.now().isoformat(),
                "retry_count": 0,
                "error_message": None
            }
            
            success_count += 1
            print(f"  ✓ 发布成功，文章ID: {article_id}")
            
        except Exception as e:
            # 同步失败，标记为failed
            print(f"  ✗ 发布失败: {e}")
            cache['files'][file_path] = {
                "status": "failed",
                "emlog_id": None,
                "md5_hash": calculate_md5(full_path),
                "last_modified": get_file_mtime(full_path),
                "sync_time": None,
                "retry_count": 1,
                "error_message": str(e)
            }
            failed_count += 1
    
    print(f"\n同步完成: 成功 {success_count} 个，失败 {failed_count} 个")
    
    return cache
```

#### 模式3：智能匹配模式（推荐已有文章时使用）

**特点：** 尝试将Git文件与Emlog已有文章建立映射关系

```python
def initialize_match_mode(md_files):
    """
    智能匹配模式：尝试将Git文件与Emlog已有文章建立映射
    """
    cache = {
        "version": "1.0",
        "last_sync": datetime.now().isoformat(),
        "files": {}
    }
    
    api = EmlogAPI(config)
    
    # 1. 获取Emlog所有文章
    emlog_articles = api.get_all_articles()
    print(f"Emlog博客现有 {len(emlog_articles)} 篇文章")
    
    # 2. 根据配置的匹配规则建立映射
    match_by = config['init']['match_by']
    matched_count = 0
    
    for file_path in md_files:
        front_matter, content = parse_markdown(file_path)
        
        # 尝试匹配
        matched_article = find_matching_article(
            front_matter, emlog_articles, match_by
        )
        
        if matched_article:
            # 找到匹配的文章
            cache['files'][file_path] = {
                "status": "synced",
                "emlog_id": matched_article['id'],
                "md5_hash": calculate_md5(file_path),
                "last_modified": get_file_mtime(file_path),
                "sync_time": datetime.now().isoformat(),
                "retry_count": 0,
                "error_message": None
            }
            matched_count += 1
            print(f"  [已匹配] {file_path} → 文章ID {matched_article['id']}")
        else:
            # 未找到匹配，标记为待同步
            cache['files'][file_path] = {
                "status": "unsynced",
                "emlog_id": None,
                "md5_hash": calculate_md5(file_path),
                "last_modified": get_file_mtime(file_path),
                "sync_time": None,
                "retry_count": 0,
                "error_message": None
            }
            print(f"  [未匹配] {file_path} (将作为新文章)")
    
    print(f"\n匹配结果: {matched_count}/{len(md_files)} 个文件已建立映射")
    
    return cache

def find_matching_article(front_matter, emlog_articles, match_by):
    """
    根据规则查找匹配的文章
    """
    title = front_matter.get('title', '')
    date = front_matter.get('date', None)
    
    for article in emlog_articles:
        if match_by == 'title':
            if article['title'] == title:
                return article
                
        elif match_by == 'date':
            if date and article['date'] == date:
                return article
                
        elif match_by == 'title_and_date':
            if article['title'] == title and date and article['date'] == date:
                return article
    
    return None
```

### 3.5 手动初始化命令

提供独立的初始化命令，方便用户控制：

```bash
# 安全模式初始化（仅建立缓存）
python sync.py --init safe

# 全量同步初始化
python sync.py --init sync

# 智能匹配初始化
python sync.py --init match

# 查看初始化状态（不执行同步）
python sync.py --init-status
```

### 3.6 初始化后的处理

初始化完成后，后续运行将进入正常的增量同步流程：

```python
def main():
    # 检查缓存文件是否存在
    if not cache_file_exists():
        cache = initialize_cache()  # 首次运行：初始化
    else:
        cache = load_cache()  # 正常运行：加载缓存
    
    # 后续进入正常流程
    sync_process(cache)
```

---

## 4. Emlog API 集成

### 3.1 API 鉴权配置

**推荐方式：免签名鉴权（配合HTTPS）**

```python
# 配置文件示例
EMLOG_CONFIG = {
    'base_url': 'https://yourdomain',
    'api_key': 'your_api_key_here',  # 从后台获取
    'author_uid': 1,                   # 作者用户ID
}
```

**所有API请求需附加参数：**
```
api_key=your_api_key
```

参考：[Emlog API文档 - 鉴权说明](https://www.emlog.net/docs/api/)

### 3.2 核心API接口

#### 3.2.1 获取分类列表

**用途：** 将 categories 转换为 sortid

```http
GET /?rest-api=sort_list&api_key={api_key}
```

**返回示例：**
```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "sorts": [
      {"sortid": "1", "sortname": "技术博客"},
      {"sortid": "2", "sortname": "大模型专区"}
    ]
  }
}
```

**处理逻辑：**
1. 缓存分类列表到内存
2. 通过分类名称查找对应的 sortid
3. 如果分类不存在，可选择创建新分类或使用默认分类

#### 3.2.2 上传图片

**用途：** 上传本地图片，获取 Emlog 图片URL

```http
POST /?rest-api=upload
Content-Type: multipart/form-data

file: (binary)
api_key: your_api_key
sid: 1  # 资源分类ID（可选）
```

**返回示例：**
```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "media_id": 80,
    "url": "https://yourdomain/content/uploadfile/202307/xxx.png"
  }
}
```

#### 3.2.3 发布文章（新增）

**用途：** 发布新文章

```http
POST /?rest-api=article_post
Content-Type: application/x-www-form-urlencoded

title: 文章标题
content: 文章内容（Markdown）
excerpt: 文章摘要
sortid: 1
tag: 标签1,标签2
cover: https://...
top: n
post_date: 1677640065
draft: n
allow_remark: y
author_uid: 1
api_key: your_api_key
```

**返回示例：**
```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "article_id": 123
  }
}
```

**重要：** 保存 `article_id` 到映射文件

#### 3.2.4 编辑文章（更新）

**用途：** 更新已存在的文章

```http
POST /?rest-api=article_draft_edit
Content-Type: application/x-www-form-urlencoded

id: 123  # 文章ID（从映射文件获取）
title: 更新后的标题
content: 更新后的内容
# ... 其他字段同发布接口
api_key: your_api_key
```

**返回示例：**
```json
{
  "code": 0,
  "msg": "ok",
  "data": ""
}
```

#### 3.2.5 删除文章

**用途：** 删除文章（当Git中文件被删除时）

```http
POST /?rest-api=article_del

id: 123
api_key: your_api_key
```

---

## 5. 核心同步流程（对比文件系统与缓存）

### 5.1 完整主流程

```python
def main():
    """
    主同步流程：
    1. 加载或创建Map缓存
    2. 扫描文件系统，对比缓存，确定各文件状态
    3. 根据状态调用Emlog API同步
    4. 更新Map缓存
    """
    
    print("=" * 60)
    print("Emlog 同步程序启动")
    print("=" * 60)
    
    # ============ 第一步：加载或初始化Map缓存 ============
    if not cache_file_exists():
        print("\n[初始化] 首次运行，创建Map缓存...")
        cache = initialize_cache()  # 详见第3章
    else:
        print("\n[加载缓存] 读取现有Map缓存...")
        cache = load_cache()
        print(f"  缓存中有 {len(cache['files'])} 个文件记录")
    
    # ============ 第二步：扫描文件系统并对比缓存 ============
    print("\n[扫描文件] 扫描工作目录...")
    work_tree = config['git']['work_tree']
    current_files = scan_markdown_files(work_tree)
    print(f"  工作目录中有 {len(current_files)} 个Markdown文件")
    
    # ============ 第三步：对比文件系统与缓存，更新状态 ============
    print("\n[状态对比] 对比文件系统与缓存...")
    cache = update_file_status(cache, current_files, work_tree)
    
    # ============ 第四步：根据状态同步到Emlog ============
    print("\n[开始同步] 根据状态同步到Emlog...")
    cache = sync_to_emlog(cache, work_tree)
    
    # ============ 第五步：保存更新后的缓存 ============
    save_cache(cache)
    
    print("\n" + "=" * 60)
    print("同步完成！")
    print("=" * 60)
```

### 5.2 步骤详解

#### 步骤3：对比文件系统与缓存，更新状态（核心逻辑）

这是整个系统的核心：通过对比文件系统和Map缓存，确定每个文件的状态。

```python
def update_file_status(cache, current_files, work_tree):
    """
    对比文件系统与缓存，确定每个文件的状态
    
    逻辑：
    1. 文件系统有 + 缓存无 → unsynced（新文件）
    2. 文件系统有 + 缓存有 + hash相同 → synced（已同步，跳过）
    3. 文件系统有 + 缓存有 + hash不同 → modified（已修改）
    4. 文件系统无 + 缓存有 → deleted（已删除）
    """
    import os
    from datetime import datetime
    
    # 统计
    stats = {
        'unsynced': 0,
        'modified': 0,
        'deleted': 0,
        'synced': 0,
        'ignored': 0
    }
    
    # 将current_files转换为集合，方便查找
    current_files_set = set(current_files)
    
    # ========== 第一轮：检查文件系统中的文件 ==========
    for file_path in current_files:
        full_path = os.path.join(work_tree, file_path)
        
        # 检查是否应该忽略
        if should_ignore_file(file_path):
            if file_path not in cache['files'] or cache['files'][file_path]['status'] != 'ignored':
                cache['files'][file_path] = {
                    "status": "ignored",
                    "emlog_id": None,
                    "md5_hash": calculate_md5(full_path),
                    "last_modified": get_file_mtime(full_path),
                    "sync_time": None,
                    "retry_count": 0,
                    "error_message": None
                }
                print(f"  [忽略] {file_path}")
            stats['ignored'] += 1
            continue
        
        current_hash = calculate_md5(full_path)
        current_mtime = get_file_mtime(full_path)
        
        # --- 情况1：缓存中没有此文件 → 新文件 ---
        if file_path not in cache['files']:
            cache['files'][file_path] = {
                "status": "unsynced",
                "emlog_id": None,
                "md5_hash": current_hash,
                "last_modified": current_mtime,
                "sync_time": None,
                "retry_count": 0,
                "error_message": None
            }
            print(f"  [新文件] {file_path}")
            stats['unsynced'] += 1
        
        # --- 情况2：缓存中有此文件 ---
        else:
            cached_info = cache['files'][file_path]
            cached_hash = cached_info.get('md5_hash', '')
            cached_status = cached_info.get('status', '')
            
            # 如果之前是failed状态，这次重新检查
            if cached_status == 'failed':
                print(f"  [重检] {file_path} (之前同步失败)")
            
            # 对比hash
            if current_hash == cached_hash:
                # Hash相同 → 文件内容未变
                if cached_status not in ['synced', 'ignored']:
                    # 之前不是synced状态，现在内容没变，但可能需要同步
                    # 保持原状态（如failed需要重试）
                    print(f"  [保持] {file_path} (状态: {cached_status})")
                else:
                    # 已同步且内容未变，跳过
                    stats['synced'] += 1
            else:
                # Hash不同 → 文件内容已变化
                if cached_status == 'synced' or cached_status == 'failed':
                    # 已同步的文件被修改了
                    cache['files'][file_path]['status'] = 'modified'
                    cache['files'][file_path]['md5_hash'] = current_hash
                    cache['files'][file_path]['last_modified'] = current_mtime
                    print(f"  [已修改] {file_path}")
                    stats['modified'] += 1
                elif cached_status == 'unsynced':
                    # 未同步的文件内容又变了，更新hash
                    cache['files'][file_path]['md5_hash'] = current_hash
                    cache['files'][file_path]['last_modified'] = current_mtime
                    print(f"  [更新未同步文件] {file_path}")
                    stats['unsynced'] += 1
                else:
                    # 其他状态，更新hash和状态
                    cache['files'][file_path]['md5_hash'] = current_hash
                    cache['files'][file_path]['last_modified'] = current_mtime
                    if cached_status != 'modified':
                        cache['files'][file_path]['status'] = 'modified'
                    print(f"  [已修改] {file_path}")
                    stats['modified'] += 1
    
    # ========== 第二轮：检查缓存中但文件系统没有的文件 → 已删除 ==========
    deleted_files = []
    for file_path in list(cache['files'].keys()):
        if file_path not in current_files_set:
            cached_info = cache['files'][file_path]
            if cached_info['status'] != 'ignored':
                # 文件不存在了，标记为删除
                cache['files'][file_path]['status'] = 'deleted'
                print(f"  [已删除] {file_path}")
                stats['deleted'] += 1
                deleted_files.append(file_path)
    
    # 输出统计
    print(f"\n状态统计:")
    print(f"  新文件 (unsynced): {stats['unsynced']} 个")
    print(f"  已修改 (modified): {stats['modified']} 个")
    print(f"  已删除 (deleted): {stats['deleted']} 个")
    print(f"  已同步 (synced): {stats['synced']} 个")
    print(f"  已忽略 (ignored): {stats['ignored']} 个")
    
    return cache
```

#### 步骤4：状态驱动同步

根据各文件的状态，调用相应的Emlog API进行同步。

```python
def sync_to_emlog(cache, work_tree):
    """
    根据缓存状态，执行相应的同步操作
    """
    import os
    from datetime import datetime
    
    api = EmlogAPI(config)
    categories = api.get_categories()
    
    # 统计信息
    stats = {
        'synced': 0,
        'created': 0,
        'updated': 0,
        'deleted': 0,
        'failed': 0,
        'skipped': 0
    }
    
    # 按状态分组，先处理删除，再处理新增和修改
    files_to_delete = []
    files_to_create = []
    files_to_update = []
    files_to_retry = []
    
    for file_path, file_info in cache['files'].items():
        status = file_info['status']
        
        if status == 'deleted':
            files_to_delete.append((file_path, file_info))
        elif status == 'unsynced':
            files_to_create.append((file_path, file_info))
        elif status == 'modified':
            files_to_update.append((file_path, file_info))
        elif status == 'failed':
            files_to_retry.append((file_path, file_info))
        elif status == 'synced':
            stats['synced'] += 1
        elif status == 'ignored':
            stats['skipped'] += 1
    
    # ========== 1. 处理删除 ==========
    for file_path, file_info in files_to_delete:
        try:
            emlog_id = file_info.get('emlog_id')
            if emlog_id:
                print(f"\n[删除文章] {file_path} (ID: {emlog_id})")
                api.delete_article(emlog_id)
                print(f"  ✓ 删除成功")
                stats['deleted'] += 1
            else:
                print(f"\n[移除缓存] {file_path} (无Emlog文章ID)")
            
            # 从缓存中移除
            del cache['files'][file_path]
            
        except Exception as e:
            print(f"  ✗ 删除失败: {e}")
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # ========== 2. 处理新增 ==========
    for file_path, file_info in files_to_create:
        try:
            full_path = os.path.join(work_tree, file_path)
            print(f"\n[发布新文章] {file_path}")
            
            article_id = publish_new_article(full_path, api, categories)
            
            # 更新缓存状态为已同步
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['emlog_id'] = article_id
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            stats['created'] += 1
            print(f"  ✓ 发布成功，文章ID: {article_id}")
            
        except Exception as e:
            print(f"  ✗ 发布失败: {e}")
            cache['files'][file_path]['status'] = 'failed'
            cache['files'][file_path]['retry_count'] = file_info.get('retry_count', 0) + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # ========== 3. 处理修改 ==========
    for file_path, file_info in files_to_update:
        try:
            full_path = os.path.join(work_tree, file_path)
            emlog_id = file_info.get('emlog_id')
            
            if not emlog_id:
                # 没有emlog_id，说明之前没同步成功，当作新文章
                print(f"\n[发布新文章] {file_path} (之前未同步)")
                article_id = publish_new_article(full_path, api, categories)
                cache['files'][file_path]['emlog_id'] = article_id
                stats['created'] += 1
            else:
                print(f"\n[更新文章] {file_path} (ID: {emlog_id})")
                update_article(full_path, emlog_id, api, categories)
                stats['updated'] += 1
            
            # 更新缓存状态
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            print(f"  ✓ 同步成功")
            
        except Exception as e:
            print(f"  ✗ 同步失败: {e}")
            cache['files'][file_path]['status'] = 'failed'
            cache['files'][file_path]['retry_count'] = file_info.get('retry_count', 0) + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # ========== 4. 处理失败重试 ==========
    max_retries = config.get('max_retries', 3)
    
    for file_path, file_info in files_to_retry:
        retry_count = file_info.get('retry_count', 0)
        
        if retry_count >= max_retries:
            print(f"\n[跳过] {file_path} (重试次数已达上限: {retry_count}/{max_retries})")
            print(f"       错误: {file_info.get('error_message', '未知错误')}")
            stats['failed'] += 1
                continue
            
        try:
            full_path = os.path.join(work_tree, file_path)
            emlog_id = file_info.get('emlog_id')
            
            print(f"\n[重试] {file_path} (第 {retry_count + 1} 次)")
            
            if emlog_id:
                # 有ID，当作更新
                update_article(full_path, emlog_id, api, categories)
                stats['updated'] += 1
            else:
                # 无ID，当作新建
                article_id = publish_new_article(full_path, api, categories)
                cache['files'][file_path]['emlog_id'] = article_id
                stats['created'] += 1
            
            # 重试成功，更新状态
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            print(f"  ✓ 重试成功")
            
        except Exception as e:
            print(f"  ✗ 重试失败: {e}")
            cache['files'][file_path]['retry_count'] = retry_count + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # 输出统计信息
    print("\n" + "=" * 50)
    print("同步统计:")
    print(f"  已同步（跳过）: {stats['synced']} 个")
    print(f"  新发布: {stats['created']} 个")
    print(f"  已更新: {stats['updated']} 个")
    print(f"  已删除: {stats['deleted']} 个")
    print(f"  同步失败: {stats['failed']} 个")
    print(f"  已忽略: {stats['skipped']} 个")
    print("=" * 50)
    
    return cache
```

### 5.3 辅助函数

```python
def publish_new_article(file_path, api, categories):
    """发布新文章到Emlog"""
            # 解析Markdown
            front_matter, content = parse_markdown(file_path)
            
            # 处理图片
            content = process_images(content, api)
            
            # 生成摘要
            excerpt = generate_ai_summary(content)
            
            # 构建API参数
            article_data = build_article_data(
                front_matter, content, excerpt, categories
            )
            
    # 调用API发布
                article_id = api.create_article(article_data)
    return article_id

def update_article(file_path, emlog_id, api, categories):
    """更新Emlog文章"""
    # 解析Markdown
    front_matter, content = parse_markdown(file_path)
    
    # 处理图片
    content = process_images(content, api)
    
    # 生成摘要
    excerpt = generate_ai_summary(content)
    
    # 构建API参数
    article_data = build_article_data(
        front_matter, content, excerpt, categories
    )
    article_data['id'] = emlog_id
    
    # 调用API更新
    api.update_article(article_data)

def calculate_md5(file_path):
    """计算文件MD5哈希"""
    import hashlib
    with open(file_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def get_file_mtime(file_path):
    """获取文件修改时间"""
    import os
    from datetime import datetime
    mtime = os.path.getmtime(file_path)
    return datetime.fromtimestamp(mtime).isoformat()

def cache_file_exists():
    """检查缓存文件是否存在"""
    cache_file = config.get('cache_file', '.emlog_cache.json')
    return os.path.exists(cache_file)

def load_cache():
    """加载缓存文件"""
    cache_file = config.get('cache_file', '.emlog_cache.json')
    with open(cache_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_cache(cache):
    """保存缓存文件"""
    cache_file = config.get('cache_file', '.emlog_cache.json')
    cache['last_sync'] = datetime.now().isoformat()
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def create_empty_cache():
    """创建空缓存结构"""
    from datetime import datetime
    return {
        "version": "1.0",
        "last_sync": datetime.now().isoformat(),
        "files": {}
    }

def should_ignore_file(file_path):
    """
    检查文件是否应该被忽略
    根据配置文件中的忽略规则判断
    """
    # 从配置读取忽略规则
    ignore_patterns = config.get('ignore_patterns', [])
    
    # 默认忽略的文件
    default_ignores = ['README.md', 'CHANGELOG.md', 'LICENSE.md']
    
    import fnmatch
    
    # 检查文件名是否在默认忽略列表
    file_name = os.path.basename(file_path)
    if file_name in default_ignores:
        return True
    
    # 检查是否匹配忽略模式
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    
    return False
```

---

## 6. 文件解析与处理

### 6.1 图片处理流程

```python
def process_images(content, api):
    """
    处理Markdown中的图片引用
    
    1. 识别本地图片：![alt](images/xxx.png)
    2. 上传到Emlog
    3. 替换为Emlog URL：![alt](https://yourdomain/content/uploadfile/xxx.png)
    4. 外部图片保持不变
    """
    import re
    
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    
    def replace_image(match):
        alt_text = match.group(1)
        image_path = match.group(2)
        
        # 外部图片不处理
        if image_path.startswith('http'):
            return match.group(0)
        
        # 上传本地图片
        uploaded_url = api.upload_image(image_path)
        return f'![{alt_text}]({uploaded_url})'
    
    return re.sub(pattern, replace_image, content)
```

### 6.2 分类处理逻辑

```python
def get_category_id(category_names, category_cache):
    """
    Front Matter中的categories是列表，取最后一个作为主分类
    
    例如：
    categories:
      - 大模型专区
      - 大模型评测
    
    使用"大模型评测"作为文章分类
    """
    if not category_names:
        return 1  # 默认分类ID
    
    target_category = category_names[-1]  # 取最后一个
    
    for cat in category_cache:
        if cat['sortname'] == target_category:
            return int(cat['sortid'])
    
    # 分类不存在时的处理
    return 1  # 使用默认分类
```

---

## 7. 配置文件设计

### 7.1 主配置文件 (`config.yaml`)

```yaml
# Emlog API配置
emlog:
  base_url: "https://yourdomain.com"
  api_key: "your_api_key_here"
  author_uid: 1
  default_category_id: 1  # 默认分类ID

# Git配置
git:
  work_tree: "/var/www/hexo"  # Git工作目录（存放Markdown文件）
  git_dir: "/var/repo/blog.git"  # Git仓库目录

# 初始化配置（首次运行时生效）
init:
  mode: "safe"  # safe（安全模式）| sync（全量同步）| match（智能匹配）
  match_by: "title"  # 匹配规则：title | date | title_and_date
  scan_existing: true  # 是否扫描Emlog已有文章

# 文件忽略规则
ignore_patterns:
  - "README.md"
  - "CHANGELOG.md"
  - "LICENSE.md"
  - "draft/*"  # 忽略draft目录下的所有文件
  - "*.draft.md"  # 忽略以.draft.md结尾的文件

# AI配置（用于生成摘要）
ai:
  enabled: true  # 是否启用AI生成摘要
  provider: "openai"  # openai | anthropic | local
  api_key: "sk-xxx"
  model: "gpt-4"
  summary_length: 100  # 摘要字数

# 缓存文件路径
cache_file: ".emlog_cache.json"

# 重试配置
max_retries: 3  # API调用失败时的最大重试次数

# 日志配置
logging:
  level: "INFO"  # DEBUG | INFO | WARNING | ERROR
  file: "emlog_sync.log"
```

---

## 8. 错误处理

### 8.1 常见错误与处理

| 错误场景 | 处理策略 |
|----------|----------|
| API鉴权失败 | 记录错误，终止同步，通知管理员 |
| 图片上传失败 | 跳过该图片，使用原路径，记录警告 |
| 分类不存在 | 使用默认分类，记录警告 |
| 文章更新失败 | 重试3次，失败则记录错误，继续处理下一个 |
| Markdown解析失败 | 跳过该文件，记录错误 |
| AI摘要生成失败 | 使用文章前100字作为摘要 |

### 8.2 日志记录

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('emlog_sync.log'),
        logging.StreamHandler()
    ]
)

# 使用示例
logging.info(f"Processing file: {file_path}")
logging.warning(f"Category not found: {category_name}, using default")
logging.error(f"Failed to upload image: {image_path}, error: {e}")
```

---

## 9. 部署与运行

### 9.1 环境要求

```
Python >= 3.8
依赖包：
  - requests  # HTTP请求
  - pyyaml    # 配置文件解析
  - markdown  # Markdown解析（可选）
  - openai    # AI摘要生成（可选）
```

### 9.2 Git Hook 集成

**文件位置：** `hooks/post-receive`

```bash
#!/bin/bash

# 检出最新代码到工作目录
git --work-tree=/var/www/hexo --git-dir=/var/repo/blog.git checkout -f

# 切换到同步脚本目录
cd /path/to/emlog-sync

# 运行同步脚本
python3 sync.py

# 记录同步结果
if [ $? -eq 0 ]; then
    echo "✓ Emlog sync completed successfully"
else
    echo "✗ Emlog sync failed, check logs at emlog_sync.log"
    exit 1
fi
```

**部署说明：**
1. Git push 触发 post-receive hook
2. Hook 执行 `checkout -f` 将文件更新到工作目录
3. 立即执行同步脚本，对比文件系统与缓存
4. 根据状态同步到 Emlog

---

## 10. 测试要点

1. **单元测试**
   - Markdown解析正确性
   - 字段映射转换
   - 图片URL替换
   - 哈希计算
   - 状态判断逻辑

2. **集成测试**
   - API调用成功
   - 文章发布/更新/删除
   - 图片上传
   - 分类查询
   - 缓存文件读写

3. **端到端测试**
   - 首次初始化（空目录、已有文件）
   - Git push 触发同步
   - 新增文章同步
   - 修改文章同步
   - 删除文章同步
   - 失败重试机制
   - 忽略文件规则

4. **边界测试**
   - 大量文件同步性能
   - 网络异常恢复
   - 并发提交处理
   - 缓存文件损坏恢复

---

## 11. 总结与优势

### 11.1 简化后的逻辑优势

1. **无需依赖 Git Diff**
   - 直接对比文件系统与缓存，逻辑更清晰
   - 不受 Git 历史记录影响
   - 更容易理解和维护

2. **状态驱动设计**
   - 每个文件都有明确的状态
   - 状态转换清晰可追溯
   - 便于故障排查和手动干预

3. **完整的初始化支持**
   - 支持已有文件的Git仓库初始化
   - 多种初始化模式适配不同场景
   - 智能匹配减少重复发布

4. **健壮的错误处理**
   - 失败自动重试机制
   - 错误信息完整记录
   - 支持人工介入修复

5. **灵活的配置**
   - 文件忽略规则
   - 可调节的重试次数
   - 多种初始化策略

### 11.2 系统运行示例

```
Emlog 同步程序启动
============================================================

[加载缓存] 读取现有Map缓存...
  缓存中有 15 个文件记录

[扫描文件] 扫描工作目录...
  工作目录中有 17 个Markdown文件

[状态对比] 对比文件系统与缓存...
  [新文件] posts/new-article.md
  [已修改] posts/updated-article.md
  [已删除] posts/old-article.md

状态统计:
  新文件 (unsynced): 1 个
  已修改 (modified): 1 个
  已删除 (deleted): 1 个
  已同步 (synced): 14 个
  已忽略 (ignored): 0 个

[开始同步] 根据状态同步到Emlog...

[删除文章] posts/old-article.md (ID: 123)
  ✓ 删除成功

[发布新文章] posts/new-article.md
  ✓ 发布成功，文章ID: 150

[更新文章] posts/updated-article.md (ID: 125)
  ✓ 同步成功

==================================================
同步统计:
  已同步（跳过）: 14 个
  新发布: 1 个
  已更新: 1 个
  已删除: 1 个
  同步失败: 0 个
  已忽略: 0 个
==================================================

============================================================
同步完成！
============================================================
```

---

## 12. 参考资料

- [Emlog API官方文档](https://www.emlog.net/docs/api/)
- Emlog版本要求：Pro版本
- API开启：后台 -> 系统设置 -> API设置 

---

**文档版本：** v2.0  
**更新日期：** 2025-10-25  
**核心改进：** 简化逻辑，移除Git Diff依赖，直接对比文件系统与缓存 

