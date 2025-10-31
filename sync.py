#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Emlog 博客同步程序
从 Git 工作目录同步 Markdown 文件到 Emlog 博客
"""

import os
import sys
import json
import hashlib
import logging
import requests
import yaml
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ==================== 配置加载 ====================

def load_config(config_path: str = "config.yaml") -> Dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        logging.error(f"配置文件不存在: {config_path}")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

# ==================== 日志配置 ====================

def setup_logging(config: Dict):
    """配置日志"""
    log_config = config.get('logging', {})
    log_level = getattr(logging, log_config.get('level', 'INFO'))
    log_file = log_config.get('file', 'emlog_sync.log')
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )

# ==================== 缓存管理 ====================

class CacheManager:
    """Map 缓存管理器"""
    
    def __init__(self, cache_file: str):
        self.cache_file = cache_file
        self.cache = self._load_or_create()
    
    def _load_or_create(self) -> Dict:
        """加载或创建缓存"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"加载缓存失败: {e}")
                return self._create_empty()
        return None  # 首次运行，返回 None
    
    def _create_empty(self) -> Dict:
        """创建空缓存"""
        return {
            "version": "1.0",
            "last_sync": datetime.now().isoformat(),
            "files": {}
        }
    
    def exists(self) -> bool:
        """检查缓存是否存在"""
        return self.cache is not None
    
    def save(self):
        """保存缓存"""
        self.cache['last_sync'] = datetime.now().isoformat()
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)
        logging.info(f"缓存已保存到: {self.cache_file}")

# ==================== 文件工具 ====================

def calculate_md5(file_path: str) -> str:
    """计算文件 MD5"""
    try:
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception as e:
        logging.error(f"计算MD5失败 {file_path}: {e}")
        return ""

def get_file_mtime(file_path: str) -> str:
    """获取文件修改时间"""
    try:
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime).isoformat()
    except Exception as e:
        logging.error(f"获取文件时间失败 {file_path}: {e}")
        return datetime.now().isoformat()

def scan_markdown_files(work_tree: str, ignore_patterns: List[str]) -> List[str]:
    """扫描工作目录中的所有 Markdown 文件"""
    md_files = []
    
    for root, dirs, files in os.walk(work_tree):
        # 排除 .git 目录
        if '.git' in dirs:
            dirs.remove('.git')
        
        for file in files:
            if file.endswith('.md'):
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, work_tree)
                
                # 检查是否应该忽略
                if not should_ignore_file(rel_path, ignore_patterns):
                    md_files.append(rel_path)
    
    return md_files

def should_ignore_file(file_path: str, ignore_patterns: List[str]) -> bool:
    """检查文件是否应该被忽略"""
    import fnmatch
    
    file_name = os.path.basename(file_path)
    default_ignores = ['README.md', 'CHANGELOG.md', 'LICENSE.md']
    
    if file_name in default_ignores:
        return True
    
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    
    return False

# ==================== Markdown 解析 ====================

def parse_markdown(file_path: str) -> Tuple[Dict, str]:
    """解析 Markdown 文件，提取 Front Matter 和正文"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取 Front Matter
        front_matter = {}
        body = content
        
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                try:
                    front_matter = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()
                except Exception as e:
                    logging.warning(f"解析 Front Matter 失败 {file_path}: {e}")
        
        return front_matter, body
    
    except Exception as e:
        logging.error(f"读取文件失败 {file_path}: {e}")
        return {}, ""

# ==================== Emlog API 客户端 ====================

class EmlogAPI:
    """Emlog API 客户端"""
    
    def __init__(self, config: Dict):
        emlog_config = config['emlog']
        self.base_url = emlog_config['base_url'].rstrip('/')
        self.api_key = emlog_config['api_key']
        self.author_uid = emlog_config['author_uid']
        self.default_category_id = emlog_config.get('default_category_id', 1)
        self.categories_cache = None
    
    def _request(self, endpoint: str, method: str = 'GET', data: Dict = None) -> Dict:
        """发送 API 请求"""
        import time
        
        url = f"{self.base_url}/?rest-api={endpoint}"
        
        if data is None:
            data = {}
        data['api_key'] = self.api_key
        
        try:
            if method == 'GET':
                response = requests.get(url, params=data, timeout=30)
            else:
                response = requests.post(url, data=data, timeout=30)
            
            response.raise_for_status()
            
            # 尝试解析JSON响应
            try:
                result = response.json()
            except ValueError as e:
                # 保存完整错误响应到文件
                error_file = f"error_response_{endpoint}_{int(time.time())}.html"
                try:
                    with open(error_file, 'w', encoding='utf-8') as f:
                        f.write(f"Endpoint: {endpoint}\n")
                        f.write(f"Status Code: {response.status_code}\n")
                        f.write(f"Headers: {response.headers}\n\n")
                        f.write(response.text)
                    logging.error(f"API 响应解析失败，完整响应已保存到: {error_file}")
                except:
                    pass
                logging.error(f"API 响应解析失败 [{endpoint}], 状态码: {response.status_code}")
                logging.error(f"响应内容前500字符: {response.text[:500]}")
                raise Exception(f"API 返回非JSON格式: {e}")
            
            # 检查API返回码
            if result.get('code') != 0:
                error_msg = result.get('msg', '未知错误')
                logging.error(f"API 错误 [{endpoint}]: {error_msg}")
                raise Exception(f"API 错误: {error_msg}")
            
            return result.get('data', {})
        
        except requests.exceptions.RequestException as e:
            logging.error(f"API 请求失败 [{endpoint}]: {e}")
            raise
    
    def get_categories(self, force_refresh: bool = False) -> List[Dict]:
        """获取分类列表（包括子分类）"""
        if self.categories_cache is None or force_refresh:
            data = self._request('sort_list')
            sorts = data.get('sorts', [])
            
            # 展平分类列表（包含子分类）
            flat_categories = []
            def flatten_categories(categories):
                for cat in categories:
                    flat_cat = {
                        'sortid': cat['sid'],  # 使用 sid 作为分类ID
                        'sortname': cat['sortname'],
                        'pid': cat['pid']
                    }
                    flat_categories.append(flat_cat)
                    
                    # 递归处理子分类
                    children = cat.get('children', [])
                    if children:
                        flatten_categories(children)
            
            flatten_categories(sorts)
            self.categories_cache = flat_categories
            
            # 调试日志
            cat_names = [f"{cat['sortname']} (ID: {cat['sortid']})" for cat in flat_categories]
            logging.debug(f"获取到的分类列表: {', '.join(cat_names)}")
        
        return self.categories_cache
    
    def get_category_id(self, category_names: List[str]) -> int:
        """根据分类名称获取分类 ID
        
        Args:
            category_names: 分类名称列表，如 ["产品", "产品观点"]
        
        Returns:
            分类ID
        """
        # 如果没有指定分类，使用默认分类
        if not category_names:
            logging.debug("未指定分类，使用默认分类")
            return self.default_category_id
        
        # 获取所有分类
        categories = self.get_categories()
        if not categories:
            logging.warning("获取分类列表为空，使用默认分类")
            return self.default_category_id
        
        # 构建分类名称到分类信息的映射
        category_map = {cat['sortname']: cat for cat in categories}
        
        # 尝试匹配最后一级分类
        target_category = category_names[-1]
        if target_category in category_map:
            category_id = int(category_map[target_category]['sortid'])
            
            # 验证父分类是否匹配
            if len(category_names) > 1:
                parent_name = category_names[-2]
                parent_id = category_map[target_category]['pid']
                
                # 查找父分类是否匹配
                for cat in categories:
                    if cat['sortid'] == parent_id and cat['sortname'] == parent_name:
                        logging.info(f"找到分类: {parent_name} -> {target_category} (ID: {category_id})")
                        return category_id
                
                # 父分类不匹配，使用默认分类
                logging.warning(f"父分类不匹配: {parent_name} -> {target_category}，使用默认分类")
                return self.default_category_id
            else:
                # 单级分类，直接使用
                logging.info(f"找到分类: {target_category} (ID: {category_id})")
                return category_id
        
        # 如果分类不存在，使用默认分类
        categories_str = ' -> '.join(category_names)
        logging.warning(f"分类不存在: {categories_str}，使用默认分类 (ID: {self.default_category_id})")
        logging.debug(f"可用分类: {list(category_map.keys())}")
        return self.default_category_id
    
    def upload_image(self, image_path: str) -> str:
        """上传图片到 Emlog"""
        try:
            with open(image_path, 'rb') as f:
                files = {'file': f}
                data = {'api_key': self.api_key}
                url = f"{self.base_url}/?rest-api=upload"
                response = requests.post(url, data=data, files=files, timeout=60)
                response.raise_for_status()
                result = response.json()
                
                if result.get('code') == 0:
                    return result['data']['url']
                else:
                    raise Exception(result.get('msg', '上传失败'))
        
        except Exception as e:
            logging.warning(f"图片上传失败 {image_path}: {e}")
            return image_path  # 返回原路径
    
    def create_article(self, article_data: Dict) -> int:
        """发布新文章"""
        # 调试：打印发送的数据
        logging.debug(f"发送文章数据: {article_data}")
        data = self._request('article_post', 'POST', article_data)
        return int(data['article_id'])
    
    def update_article(self, article_data: Dict):
        """更新文章
        
        使用 article_draft_edit 接口来更新文章
        """
        # 使用 article_draft_edit 接口更新文章
        self._request('article_update', 'POST', article_data)
    
    def delete_article(self, article_id: int):
        """删除文章"""
        self._request('article_del', 'POST', {'id': article_id})

# ==================== 文章处理 ====================

def process_images(content: str, work_tree: str, api: EmlogAPI) -> str:
    """处理 Markdown 中的图片"""
    import urllib.parse
    from urllib.parse import urlparse
    
    pattern = r'!\[([^\]]*)\]\(([^)]+)\)'
    
    def replace_image(match):
        alt_text = match.group(1)
        image_path = match.group(2)
        
        # 检查是否是远程URL
        if image_path.startswith('http'):
            # 检查是否已经是Emlog服务器的图片（避免重复上传）
            
            try:
                base_parsed = urlparse(api.base_url)
                image_parsed = urlparse(image_path)
                
                # 比较域名（包括端口号）
                base_netloc = base_parsed.netloc.lower()
                image_netloc = image_parsed.netloc.lower()
                
                # 如果图片URL的域名与Emlog服务器域名相同，说明已经上传过，不处理
                if base_netloc == image_netloc:
                    logging.debug(f"图片已存在于Emlog服务器，跳过上传: {image_path}")
                    return match.group(0)
            except Exception as e:
                logging.debug(f"解析URL失败，跳过域名检查: {e}")
            
            # 外部其他服务器的图片，不处理
            return match.group(0)
        
        # URL解码（处理 %20 等编码）
        decoded_path = urllib.parse.unquote(image_path)
        
        # 处理相对路径，确保相对于 work_tree
        # 如果是 /images/xxx.png 格式，去掉开头的 /
        if decoded_path.startswith('/'):
            decoded_path = decoded_path.lstrip('/')
        
        # 构建完整路径
        full_path = os.path.join(work_tree, decoded_path)
        
        if os.path.exists(full_path):
            try:
                uploaded_url = api.upload_image(full_path)
                logging.info(f"图片上传成功: {decoded_path} -> {uploaded_url}")
                return f'![{alt_text}]({uploaded_url})'
            except Exception as e:
                logging.warning(f"图片上传失败 {decoded_path}: {e}")
                return match.group(0)
        else:
            logging.warning(f"图片不存在: {decoded_path} (完整路径: {full_path})")
            return match.group(0)
    
    return re.sub(pattern, replace_image, content)

def generate_excerpt(content: str, max_length: int = 100) -> str:
    """生成文章摘要（简单截取前N字）"""
    # 移除 Markdown 标记
    text = re.sub(r'[#*`\[\]()!]', '', content)
    text = re.sub(r'\s+', ' ', text).strip()
    
    if len(text) <= max_length:
        return text
    
    return text[:max_length] + '...'

def build_article_data(front_matter: Dict, content: str, excerpt: str, 
                       api: EmlogAPI, work_tree: str = None) -> Dict:
    """构建文章数据"""
    # 基本字段
    data = {
        'title': front_matter.get('title', '无标题'),
        'content': content,
        'excerpt': excerpt,
        'author_uid': api.author_uid,
    }
    
    # 分类
    categories = front_matter.get('categories', [])
    if isinstance(categories, str):
        categories = [categories]
    data['sort_id'] = api.get_category_id(categories)
    
    # 标签
    tags = front_matter.get('tags', [])
    if isinstance(tags, list):
        data['tags'] = ','.join(tags)
    elif isinstance(tags, str):
        data['tags'] = tags
    
    # 封面（如果是本地路径，需要先上传到 Emlog）
    cover = front_matter.get('cover', '')
    if cover and cover.strip():
        # 检查是否是本地路径
        if not cover.startswith('http'):
            # 尝试上传封面图
            if work_tree:
                import urllib.parse
                # 移除开头的斜杠，URL解码
                cover_path = cover.lstrip('/')
                cover_path = urllib.parse.unquote(cover_path)
                full_cover_path = os.path.join(work_tree, cover_path)
                
                if os.path.exists(full_cover_path):
                    try:
                        # 上传封面图
                        uploaded_url = api.upload_image(full_cover_path)
                        data['cover'] = uploaded_url
                        logging.info(f"封面图上传成功: {cover_path} -> {uploaded_url}")
                    except Exception as e:
                        logging.warning(f"封面图上传失败: {cover_path}, 错误: {e}")
                        # 上传失败，使用原路径（可能无法显示）
                        data['cover'] = cover
                else:
                    logging.warning(f"封面图不存在: {full_cover_path}")
                    # 文件不存在，仍然使用原路径
                    data['cover'] = cover
            else:
                # 没有 work_tree，使用原路径
                data['cover'] = cover
        else:
            # 已经是完整URL，直接使用
            data['cover'] = cover
    
    # 置顶
    sticky = front_matter.get('sticky', '0')
    data['top'] = 'y' if str(sticky) == '1' else 'n'
    
    # 发布状态
    published = front_matter.get('published', 'true')
    data['draft'] = 'n' if str(published).lower() == 'true' else 'y'
    
    # 评论
    comments = front_matter.get('comments')
    if comments is not None:
        data['allow_remark'] = 'n' if str(comments).lower() == 'false' else 'y'
    else:
        data['allow_remark'] = 'y'
    
    # 别名
    alias = front_matter.get('alias')
    if alias:
        data['alias'] = alias
    
    # 发布时间（Emlog使用发布时间，如：2022-05-03 23:30:16）
    date = front_matter.get('date')
    if date:
        try:
            if isinstance(date, str):
                # 移除时区信息，假设为本地时间
                date_str = date.replace('Z', '').replace('+00:00', '').strip()
                # 尝试多种日期格式
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d']:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        break
                    except:
                        continue
                else:
                    # 如果都失败，尝试 ISO 格式
                    dt = datetime.fromisoformat(date_str)
            else:
                dt = date
            data['post_date'] = dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logging.warning(f"日期解析失败: {date}, 使用当前时间")
            data['post_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    else:
        # 没有日期，使用当前时间
        data['post_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    return data

def publish_article(file_path: str, work_tree: str, api: EmlogAPI, config: Dict) -> int:
    """发布文章"""
    full_path = os.path.join(work_tree, file_path)
    front_matter, content = parse_markdown(full_path)
    
    # 处理图片
    content = process_images(content, work_tree, api)
    
    # 生成摘要
    ai_config = config.get('ai', {})
    if ai_config.get('enabled', False):
        # TODO: 集成 AI 生成摘要
        excerpt = generate_excerpt(content, ai_config.get('summary_length', 100))
    else:
        excerpt = generate_excerpt(content, 100)
    
    # 构建数据
    article_data = build_article_data(front_matter, content, excerpt, api, work_tree)
    
    # 发布
    article_id = api.create_article(article_data)
    logging.info(f"发布成功: {file_path} -> 文章ID: {article_id}")
    
    return article_id

def update_article(file_path: str, article_id: int, work_tree: str, 
                   api: EmlogAPI, config: Dict):
    """更新文章"""
    full_path = os.path.join(work_tree, file_path)
    front_matter, content = parse_markdown(full_path)
    
    # 处理图片
    content = process_images(content, work_tree, api)
    
    # 生成摘要
    ai_config = config.get('ai', {})
    if ai_config.get('enabled', False):
        excerpt = generate_excerpt(content, ai_config.get('summary_length', 100))
    else:
        excerpt = generate_excerpt(content, 100)
    
    # 构建数据
    article_data = build_article_data(front_matter, content, excerpt, api, work_tree)
    article_data['id'] = article_id
    
    # 更新
    api.update_article(article_data)
    logging.info(f"更新成功: {file_path} (ID: {article_id})")

# ==================== 初始化 ====================

def initialize_cache(config: Dict, cache_manager: CacheManager) -> Dict:
    """首次初始化缓存"""
    print("=" * 60)
    print("检测到首次运行，开始初始化...")
    print("=" * 60)
    
    work_tree = config['git']['work_tree']
    ignore_patterns = config.get('ignore_patterns', [])
    
    # 扫描文件
    all_files = scan_markdown_files(work_tree, ignore_patterns)
    print(f"发现 {len(all_files)} 个 Markdown 文件")
    
    if len(all_files) == 0:
        print("工作目录为空，创建空缓存")
        cache = cache_manager._create_empty()
        cache_manager.cache = cache
        return cache
    
    # 获取初始化模式
    init_config = config.get('init', {})
    mode = init_config.get('mode', 'safe')
    print(f"初始化模式: {mode}")
    
    if mode == 'safe':
        cache = initialize_safe_mode(all_files, work_tree, cache_manager)
    elif mode == 'sync':
        cache = initialize_sync_mode(all_files, work_tree, config, cache_manager)
    elif mode == 'match':
        cache = initialize_match_mode(all_files, work_tree, config, cache_manager)
    else:
        print(f"未知的初始化模式: {mode}，使用安全模式")
        cache = initialize_safe_mode(all_files, work_tree, cache_manager)
    
    print("=" * 60)
    print("初始化完成！")
    print("=" * 60)
    
    return cache

def initialize_safe_mode(files: List[str], work_tree: str, 
                         cache_manager: CacheManager) -> Dict:
    """安全模式：仅建立缓存，不同步"""
    cache = cache_manager._create_empty()
    
    for file_path in files:
        full_path = os.path.join(work_tree, file_path)
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
    
    print(f"\n共扫描 {len(files)} 个文件，已标记为 unsynced")
    print("提示: 下次运行将自动同步这些文件到 Emlog")
    
    return cache

def initialize_sync_mode(files: List[str], work_tree: str, config: Dict,
                         cache_manager: CacheManager) -> Dict:
    """全量同步模式：立即同步所有文件"""
    cache = cache_manager._create_empty()
    api = EmlogAPI(config)
    
    success_count = 0
    failed_count = 0
    
    for file_path in files:
        full_path = os.path.join(work_tree, file_path)
        print(f"\n正在同步: {file_path}")
        
        try:
            article_id = publish_article(file_path, work_tree, api, config)
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
            print(f"  ✗ 发布失败: {e}")
    
    print(f"\n同步完成: 成功 {success_count} 个，失败 {failed_count} 个")
    return cache

def initialize_match_mode(files: List[str], work_tree: str, config: Dict,
                          cache_manager: CacheManager) -> Dict:
    """智能匹配模式：匹配 Emlog 已有文章"""
    # 简化实现：当作安全模式处理
    print("智能匹配模式暂未实现，使用安全模式")
    return initialize_safe_mode(files, work_tree, cache_manager)

# ==================== 状态更新 ====================

def update_file_status(cache: Dict, current_files: List[str], 
                       work_tree: str, ignore_patterns: List[str], force_update: bool = False) -> Dict:
    """对比文件系统与缓存，更新状态
    
    Args:
        cache: 缓存数据
        current_files: 当前文件列表
        work_tree: 工作目录
        ignore_patterns: 忽略模式列表
        force_update: 是否强制更新所有文件（包括已同步的）
    """
    stats = {
        'unsynced': 0,
        'modified': 0,
        'deleted': 0,
        'synced': 0,
        'ignored': 0,
        'force_updated': 0
    }
    
    current_files_set = set(current_files)
    
    # 检查文件系统中的文件
    for file_path in current_files:
        full_path = os.path.join(work_tree, file_path)
        current_hash = calculate_md5(full_path)
        current_mtime = get_file_mtime(full_path)
        
        # 新文件
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
        
        # 已有文件
        else:
            cached_info = cache['files'][file_path]
            cached_hash = cached_info.get('md5_hash', '')
            cached_status = cached_info.get('status', '')
            
            # 强制更新模式：将所有已同步的文件标记为需要更新
            if force_update and cached_status == 'synced':
                cache['files'][file_path]['status'] = 'modified'
                cache['files'][file_path]['md5_hash'] = current_hash
                cache['files'][file_path]['last_modified'] = current_mtime
                print(f"  [强制更新] {file_path}")
                stats['force_updated'] += 1
            # 对比 hash
            elif current_hash == cached_hash:
                if cached_status == 'synced':
                    stats['synced'] += 1
                elif cached_status == 'failed':
                    print(f"  [重检] {file_path} (之前同步失败)")
            else:
                # 内容变化
                if cached_status in ['synced', 'failed']:
                    cache['files'][file_path]['status'] = 'modified'
                    cache['files'][file_path]['md5_hash'] = current_hash
                    cache['files'][file_path]['last_modified'] = current_mtime
                    print(f"  [已修改] {file_path}")
                    stats['modified'] += 1
                elif cached_status == 'unsynced':
                    cache['files'][file_path]['md5_hash'] = current_hash
                    cache['files'][file_path]['last_modified'] = current_mtime
                    print(f"  [更新未同步文件] {file_path}")
                    stats['unsynced'] += 1
    
    # 检查已删除的文件
    for file_path in list(cache['files'].keys()):
        if file_path not in current_files_set:
            cached_info = cache['files'][file_path]
            if cached_info['status'] != 'ignored':
                cache['files'][file_path]['status'] = 'deleted'
                print(f"  [已删除] {file_path}")
                stats['deleted'] += 1
    
    # 输出统计
    print(f"\n状态统计:")
    print(f"  新文件 (unsynced): {stats['unsynced']} 个")
    print(f"  已修改 (modified): {stats['modified']} 个")
    print(f"  已删除 (deleted): {stats['deleted']} 个")
    print(f"  已同步 (synced): {stats['synced']} 个")
    if force_update and stats['force_updated'] > 0:
        print(f"  强制更新 (force_updated): {stats['force_updated']} 个")
    
    return cache

# ==================== 同步执行 ====================

def sync_to_emlog(cache: Dict, work_tree: str, config: Dict, force_update: bool = False) -> Dict:
    """根据状态同步到 Emlog
    
    Args:
        cache: 缓存数据
        work_tree: 工作目录
        config: 配置信息
        force_update: 是否强制更新（即使重试次数达到上限也要同步）
    """
    import time
    
    api = EmlogAPI(config)
    max_retries = config.get('max_retries', 3)
    request_delay = config.get('sync', {}).get('request_delay', 0.5)  # 默认500ms延迟
    
    stats = {
        'synced': 0,
        'created': 0,
        'updated': 0,
        'deleted': 0,
        'failed': 0
    }
    
    # 按状态分组
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
    
    # 1. 处理删除
    for file_path, file_info in files_to_delete:
        try:
            emlog_id = file_info.get('emlog_id')
            if emlog_id:
                print(f"\n[删除文章] {file_path} (ID: {emlog_id})")
                api.delete_article(emlog_id)
                print(f"  ✓ 删除成功")
                stats['deleted'] += 1
            
            del cache['files'][file_path]
        
        except Exception as e:
            logging.error(f"删除失败 {file_path}: {e}")
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # 2. 处理新增
    for file_path, file_info in files_to_create:
        try:
            print(f"\n[发布新文章] {file_path}")
            article_id = publish_article(file_path, work_tree, api, config)
            
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['emlog_id'] = article_id
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            stats['created'] += 1
            print(f"  ✓ 发布成功，文章ID: {article_id}")
            
            # 添加延迟避免请求过快
            time.sleep(request_delay)
        
        except Exception as e:
            logging.error(f"发布失败 {file_path}: {e}")
            cache['files'][file_path]['status'] = 'failed'
            cache['files'][file_path]['retry_count'] = file_info.get('retry_count', 0) + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # 3. 处理修改
    for file_path, file_info in files_to_update:
        try:
            emlog_id = file_info.get('emlog_id')
            
            if not emlog_id:
                print(f"\n[发布新文章] {file_path} (之前未同步)")
                article_id = publish_article(file_path, work_tree, api, config)
                cache['files'][file_path]['emlog_id'] = article_id
                stats['created'] += 1
            else:
                print(f"\n[更新文章] {file_path} (ID: {emlog_id})")
                update_article(file_path, emlog_id, work_tree, api, config)
                stats['updated'] += 1
            
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            print(f"  ✓ 同步成功")
            
            # 添加延迟避免请求过快
            time.sleep(request_delay)
        
        except Exception as e:
            logging.error(f"同步失败 {file_path}: {e}")
            cache['files'][file_path]['status'] = 'failed'
            cache['files'][file_path]['retry_count'] = file_info.get('retry_count', 0) + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # 4. 处理失败重试
    for file_path, file_info in files_to_retry:
        retry_count = file_info.get('retry_count', 0)
        
        # 强制更新模式下，即使达到重试上限也要继续尝试
        if retry_count >= max_retries and not force_update:
            print(f"\n[跳过] {file_path} (重试次数已达上限: {retry_count}/{max_retries})")
            print(f"       错误: {file_info.get('error_message', '未知错误')}")
            stats['failed'] += 1
            continue
        elif retry_count >= max_retries and force_update:
            print(f"\n[强制重试] {file_path} (重试次数已达上限: {retry_count}/{max_retries}，强制同步)")
            print(f"       错误: {file_info.get('error_message', '未知错误')}")
        
        try:
            emlog_id = file_info.get('emlog_id')
            if force_update and retry_count >= max_retries:
                print(f"\n[强制重试] {file_path} (第 {retry_count + 1} 次，强制模式)")
            else:
                print(f"\n[重试] {file_path} (第 {retry_count + 1} 次)")
            
            if emlog_id:
                update_article(file_path, emlog_id, work_tree, api, config)
                stats['updated'] += 1
            else:
                article_id = publish_article(file_path, work_tree, api, config)
                cache['files'][file_path]['emlog_id'] = article_id
                stats['created'] += 1
            
            cache['files'][file_path]['status'] = 'synced'
            cache['files'][file_path]['sync_time'] = datetime.now().isoformat()
            cache['files'][file_path]['retry_count'] = 0
            cache['files'][file_path]['error_message'] = None
            
            print(f"  ✓ 重试成功")
            
            # 添加延迟避免请求过快
            time.sleep(request_delay)
        
        except Exception as e:
            logging.error(f"重试失败 {file_path}: {e}")
            cache['files'][file_path]['retry_count'] = retry_count + 1
            cache['files'][file_path]['error_message'] = str(e)
            stats['failed'] += 1
    
    # 输出统计
    print("\n" + "=" * 50)
    print("同步统计:")
    print(f"  已同步（跳过）: {stats['synced']} 个")
    print(f"  新发布: {stats['created']} 个")
    print(f"  已更新: {stats['updated']} 个")
    print(f"  已删除: {stats['deleted']} 个")
    print(f"  同步失败: {stats['failed']} 个")
    print("=" * 50)
    
    return cache

# ==================== 主程序 ====================

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='Emlog 博客同步程序')
    parser.add_argument('--forceupdate', action='store_true', 
                       help='强制更新所有文件，包括已同步的文件')
    return parser.parse_args()

def main():
    """主程序"""
    # 解析命令行参数
    args = parse_arguments()
    
    print("=" * 60)
    print("Emlog 同步程序启动")
    if args.forceupdate:
        print("模式: 强制更新模式")
    print("=" * 60)
    
    # 加载配置
    config = load_config()
    setup_logging(config)
    
    # 初始化缓存管理器
    cache_file = config.get('cache_file', '.emlog_cache.json')
    cache_manager = CacheManager(cache_file)
    
    # 检查是否首次运行
    if not cache_manager.exists():
        print("\n[初始化] 首次运行，创建 Map 缓存...")
        cache = initialize_cache(config, cache_manager)
        cache_manager.cache = cache
        cache_manager.save()
        
        # 如果是安全模式，初始化后不立即同步
        init_mode = config.get('init', {}).get('mode', 'safe')
        if init_mode == 'safe':
            print("\n提示: 安全模式初始化完成，下次运行将同步文件")
            return
    else:
        print("\n[加载缓存] 读取现有 Map 缓存...")
        print(f"  缓存中有 {len(cache_manager.cache['files'])} 个文件记录")
    
    # 扫描文件系统
    print("\n[扫描文件] 扫描工作目录...")
    work_tree = config['git']['work_tree']
    ignore_patterns = config.get('ignore_patterns', [])
    current_files = scan_markdown_files(work_tree, ignore_patterns)
    print(f"  工作目录中有 {len(current_files)} 个 Markdown 文件")
    
    # 对比状态
    print("\n[状态对比] 对比文件系统与缓存...")
    cache_manager.cache = update_file_status(
        cache_manager.cache, current_files, work_tree, ignore_patterns, args.forceupdate
    )
    
    # 同步到 Emlog
    print("\n[开始同步] 根据状态同步到 Emlog...")
    cache_manager.cache = sync_to_emlog(cache_manager.cache, work_tree, config, args.forceupdate)
    
    # 保存缓存
    cache_manager.save()
    
    print("\n" + "=" * 60)
    print("同步完成！")
    print("=" * 60)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(0)
    except Exception as e:
        logging.error(f"程序异常: {e}", exc_info=True)
        sys.exit(1)

