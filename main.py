# coding=utf-8

import json
import os
import random
import re
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union

import pytz
import requests
import yaml
def clean_old_files_recursive(folder_path, days=30):
    expire_seconds = days * 24 * 60 * 60
    now = time.time()
    if not os.path.exists(folder_path):
        return

    # 倒序遍历，避免删除目录时影响遍历
    for root, dirs, files in os.walk(folder_path, topdown=False):
        # 先删过期文件
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.isfile(file_path):
                mtime = os.path.getmtime(file_path)
                if now - mtime > expire_seconds:
                    os.remove(file_path)
                    print(f"删除过期文件: {file_path}")
        # 再删空/过期文件夹（可选）
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                os.rmdir(dir_path)  # 仅删除空文件夹
                print(f"删除空目录: {dir_path}")
            except OSError:
                pass

# 调用
output_dir = "./output"
clean_old_files_recursive(output_dir, days=30)
# API 功能为可选依赖，尝试导入 Flask
try:
    from flask import Flask, jsonify, send_from_directory

    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False


VERSION = "2.2.0"


# === 配置管理 ===
def load_config():
    """加载配置文件"""
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")

    if not Path(config_path).exists():
        raise FileNotFoundError(f"配置文件 {config_path} 不存在")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)

    print(f"配置文件加载成功: {config_path}")

    # 构建配置
    config = {
        "BASE_URL": config_data["app"].get("base_url", ""),
        "VERSION_CHECK_URL": config_data["app"]["version_check_url"],
        "SHOW_VERSION_UPDATE": config_data["app"]["show_version_update"],
        "REQUEST_INTERVAL": config_data["crawler"]["request_interval"],
        "REPORT_MODE": config_data["report"]["mode"],
        "RANK_THRESHOLD": config_data["report"]["rank_threshold"],
        "USE_PROXY": config_data["crawler"]["use_proxy"],
        "DEFAULT_PROXY": config_data["crawler"]["default_proxy"],
        "ENABLE_CRAWLER": config_data["crawler"]["enable_crawler"],
        "ENABLE_NOTIFICATION": config_data["notification"]["enable_notification"],
        "MESSAGE_BATCH_SIZE": config_data["notification"]["message_batch_size"],
        "BATCH_SEND_INTERVAL": config_data["notification"]["batch_send_interval"],
        "FEISHU_MESSAGE_SEPARATOR": config_data["notification"][
            "feishu_message_separator"
        ],
        "SILENT_PUSH": {
            "ENABLED": config_data["notification"]
            .get("silent_push", {})
            .get("enabled", False),
            "TIME_RANGE": {
                "START": config_data["notification"]
                .get("silent_push", {})
                .get("time_range", {})
                .get("start", "08:00"),
                "END": config_data["notification"]
                .get("silent_push", {})
                .get("time_range", {})
                .get("end", "22:00"),
            },
            "ONCE_PER_DAY": config_data["notification"]
            .get("silent_push", {})
            .get("once_per_day", True),
            "RECORD_RETENTION_DAYS": config_data["notification"]
            .get("silent_push", {})
            .get("push_record_retention_days", 7),
        },
        "WEIGHT_CONFIG": {
            "RANK_WEIGHT": config_data["weight"]["rank_weight"],
            "FREQUENCY_WEIGHT": config_data["weight"]["frequency_weight"],
            "HOTNESS_WEIGHT": config_data["weight"]["hotness_weight"],
        },
        "PLATFORMS": config_data["platforms"],
    }

    # Webhook配置（环境变量优先）
    notification = config_data.get("notification", {})
    webhooks = notification.get("webhooks", {})

    config["FEISHU_WEBHOOK_URL"] = os.environ.get(
        "FEISHU_WEBHOOK_URL", ""
    ).strip() or webhooks.get("feishu_url", "")
    config["DINGTALK_WEBHOOK_URL"] = os.environ.get(
        "DINGTALK_WEBHOOK_URL", ""
    ).strip() or webhooks.get("dingtalk_url", "")
    config["WEWORK_WEBHOOK_URL"] = os.environ.get(
        "WEWORK_WEBHOOK_URL", ""
    ).strip() or webhooks.get("wework_url", "")
    config["TELEGRAM_BOT_TOKEN"] = os.environ.get(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip() or webhooks.get("telegram_bot_token", "")
    config["TELEGRAM_CHAT_ID"] = os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    ).strip() or webhooks.get("telegram_chat_id", "")

    # 输出配置来源信息
    webhook_sources = []
    if config["FEISHU_WEBHOOK_URL"]:
        source = "环境变量" if os.environ.get("FEISHU_WEBHOOK_URL") else "配置文件"
        webhook_sources.append(f"飞书({source})")
    if config["DINGTALK_WEBHOOK_URL"]:
        source = "环境变量" if os.environ.get("DINGTALK_WEBHOOK_URL") else "配置文件"
        webhook_sources.append(f"钉钉({source})")
    if config["WEWORK_WEBHOOK_URL"]:
        source = "环境变量" if os.environ.get("WEWORK_WEBHOOK_URL") else "配置文件"
        webhook_sources.append(f"企业微信({source})")
    if config["TELEGRAM_BOT_TOKEN"] and config["TELEGRAM_CHAT_ID"]:
        token_source = (
            "环境变量" if os.environ.get("TELEGRAM_BOT_TOKEN") else "配置文件"
        )
        chat_source = "环境变量" if os.environ.get("TELEGRAM_CHAT_ID") else "配置文件"
        webhook_sources.append(f"Telegram({token_source}/{chat_source})")

    if webhook_sources:
        print(f"Webhook 配置来源: {', '.join(webhook_sources)}")
    else:
        print("未配置任何 Webhook")

    # 加载阿里云通义千问配置
    aliyun_config = config_data.get("aliyun_qwen", {})
    config["ALIYUN_QWEN"] = {
        "ENABLED": aliyun_config.get("enabled", False),
        "API_KEY": os.environ.get("ALIYUN_API_KEY", "").strip() or aliyun_config.get("api_key", ""),
        "MODEL": aliyun_config.get("model"),  # 必须在 config.yaml 中明确配置
        "MAX_TOKENS": aliyun_config.get("max_tokens", 2000),
        "TEMPERATURE": aliyun_config.get("temperature", 0.7),
        "ENABLE_NEWS_ANALYSIS": aliyun_config.get("enable_news_analysis", False),
        "ENABLE_COPYWRITING": aliyun_config.get("enable_copywriting", False),
        "NEWS_FILTER_PROMPT": aliyun_config.get("news_filter_prompt", ""),
        "COPYWRITING_PROMPT": aliyun_config.get("copywriting_prompt", ""),
    }
    
    # 加载备用API配置（SiliconFlow）
    backup_config = config_data.get("backup_api", {})
    config["BACKUP_API"] = {
        "ENABLED": backup_config.get("enabled", False),
        "API_KEY": os.environ.get("SIJILIUDONG_API_API_KEY", "").strip() or backup_config.get("api_key", ""),
        "MODEL": backup_config.get("model", "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"),
        "BASE_URL": backup_config.get("base_url", "https://api.siliconflow.cn/v1"),
        "MAX_TOKENS": backup_config.get("max_tokens", 2000),
        "TEMPERATURE": backup_config.get("temperature", 0.7),
    }

    if config["ALIYUN_QWEN"]["ENABLED"]:
        print("阿里云通义千问AI功能已启用")
        
        # 检查模型配置
        if not config["ALIYUN_QWEN"]["MODEL"]:
            print("  ⚠️ 警告：未在 config.yaml 中配置模型名称，AI功能将不可用")
            config["ALIYUN_QWEN"]["ENABLED"] = False
        elif config["ALIYUN_QWEN"]["API_KEY"]:
            print(f"  模型: {config['ALIYUN_QWEN']['MODEL']}")
            # 调试：显示API Key的前几位和后几位，确保加载成功
            api_key = config["ALIYUN_QWEN"]["API_KEY"]
            if len(api_key) > 8:
                print(f"  API Key: {api_key[:4]}...{api_key[-4:]}")
            else:
                print(f"  API Key: {api_key}")
            if config["ALIYUN_QWEN"]["ENABLE_NEWS_ANALYSIS"]:
                print("  新闻智能分析: 已启用")
            if config["ALIYUN_QWEN"]["ENABLE_COPYWRITING"]:
                print("  文案生成: 已启用")
        else:
            print("  ⚠️ 警告：未配置API Key，AI功能将不可用")

    # 加载小红书配置
    xiaohongshu_config = config_data.get("xiaohongshu", {})
    config["XIAOHONGSHU"] = {
        "ENABLED": xiaohongshu_config.get("enabled", True),
        "TARGET_AUDIENCE": xiaohongshu_config.get("target_audience", "18-34岁一二线城市高知女性"),
        "NEWS_CATEGORIES": xiaohongshu_config.get("news_categories", []),
    }

    if config["XIAOHONGSHU"]["ENABLED"]:
        print("小红书文案生成功能已启用")
        print(f"  目标受众: {config['XIAOHONGSHU']['TARGET_AUDIENCE']}")

    return config


print("正在加载配置...")
CONFIG = load_config()
print(f"TrendRadar v{VERSION} 配置加载完成")
print(f"监控平台数量: {len(CONFIG['PLATFORMS'])}")


# === 通用AI大模型客户端 ===
class OpenAICompatibleClient:
    """通用OpenAI兼容API客户端，支持阿里云、SiliconFlow等服务"""

    _consecutive_failures = 0  # 连续失败次数，用于快速降级
    _current_provider = "aliyun"  # 当前使用的服务商

    def __init__(self, api_key: str, model: str, 
                 max_tokens: int = 2000, temperature: float = 0.7,
                 base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 provider_name: str = "aliyun"):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.base_url = base_url
        self.provider_name = provider_name
        self.api_url = f"{base_url}/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    
    @classmethod
    def reset_consecutive_failures(cls):
        """重置连续失败计数"""
        cls._consecutive_failures = 0
        print(f"API连续失败计数已重置")
    
    @classmethod
    def switch_provider(cls, provider_name: str):
        """切换服务商"""
        cls._current_provider = provider_name
        cls._consecutive_failures = 0
        print(f"已切换到 {provider_name} 服务商")

    def chat(self, messages: list, system_prompt: str = None) -> Optional[str]:
        """
        发送聊天请求（使用兼容OpenAI格式）
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词（可选）
            
        Returns:
            模型回复内容，失败返回None
        """
        import time
        
        # 连续失败次数过多时直接降级，避免长时间阻塞
        if OpenAICompatibleClient._consecutive_failures >= 3:
            print(f"{self.provider_name} API连续失败{OpenAICompatibleClient._consecutive_failures}次，跳过调用")
            return None

        max_retries = 2  # 最大重试次数
        
        for attempt in range(max_retries + 1):
            try:
                # 复制消息列表以避免修改原始数据
                messages_copy = [m.copy() for m in messages]
                
                if system_prompt:
                    messages_copy.insert(0, {"role": "system", "content": system_prompt})

                payload = {
                    "model": self.model,
                    "messages": messages_copy,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                }

                # 调试：打印实际发送的headers（隐藏API Key）
                debug_headers = {k: (v[:8] + "..." if k == "Authorization" and len(v) > 8 else v) for k, v in self.headers.items()}
                print(f"[{self.provider_name}] 发送的Headers: {debug_headers}")
                print(f"[{self.provider_name}] 发送的Payload model: {payload['model']}")
                print(f"[{self.provider_name}] API URL: {self.api_url}")

                response = requests.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=120
                )

                # 调试：打印完整的响应状态和内容
                print(f"[{self.provider_name}] API响应状态码: {response.status_code}")
                print(f"[{self.provider_name}] API完整响应: {response.text}")

                if response.status_code == 200:
                    OpenAICompatibleClient._consecutive_failures = 0
                    result = response.json()
                    # OpenAI兼容格式解析
                    if result.get("choices", []) and len(result["choices"]) > 0:
                        choice = result["choices"][0]
                        if choice.get("message", {}).get("content"):
                            return choice["message"]["content"]
                    # 尝试其他格式
                    print(f"[{self.provider_name}] API响应格式: {result}")
                    return str(result)
                else:
                    OpenAICompatibleClient._consecutive_failures += 1
                    print(f"[{self.provider_name}] API请求失败: {response.status_code} - {response.text}")
                    if attempt < max_retries:
                        wait_time = 2 ** attempt  # 指数退避
                        print(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                    return None

            except Exception as e:
                OpenAICompatibleClient._consecutive_failures += 1
                print(f"[{self.provider_name}] API调用异常(第{attempt+1}次): {e}")
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # 指数退避
                    print(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                return None


class AINewsAnalyzer:
    """AI新闻分析器，支持主API和备用API"""

    def __init__(self):
        self.config = CONFIG.get("ALIYUN_QWEN", {})
        self.backup_config = CONFIG.get("BACKUP_API", {})
        self.enabled = self.config.get("ENABLED", False)
        self.client = None
        self.backup_client = None
        self.current_client = None
        
        # 初始化主API客户端（阿里云）
        if self.enabled and self.config.get("API_KEY"):
            OpenAICompatibleClient.reset_consecutive_failures()
            self.client = OpenAICompatibleClient(
                api_key=self.config["API_KEY"],
                model=self.config["MODEL"],
                max_tokens=self.config["MAX_TOKENS"],
                temperature=self.config["TEMPERATURE"],
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                provider_name="阿里云"
            )
            self.current_client = self.client
        
        # 初始化备用API客户端（SiliconFlow）
        if self.backup_config.get("ENABLED") and self.backup_config.get("API_KEY"):
            self.backup_client = OpenAICompatibleClient(
                api_key=self.backup_config["API_KEY"],
                model=self.backup_config["MODEL"],
                max_tokens=self.backup_config["MAX_TOKENS"],
                temperature=self.backup_config["TEMPERATURE"],
                base_url=self.backup_config["BASE_URL"],
                provider_name="SiliconFlow"
            )
            print(f"备用API(SiliconFlow)已配置，模型: {self.backup_config['MODEL']}")

    def is_available(self) -> bool:
        """检查AI功能是否可用（包括备用API）"""
        return self.enabled and (self.client is not None or self.backup_client is not None)
    
    def _try_switch_backup(self) -> bool:
        """尝试切换到备用API"""
        if self.backup_client and self.current_client != self.backup_client:
            OpenAICompatibleClient.switch_provider("SiliconFlow")
            self.current_client = self.backup_client
            print("已切换到备用API(SiliconFlow)")
            return True
        return False

    def analyze_news_batch(self, news_list: List[Dict]) -> List[Dict]:
        """
        批量分析新闻，筛选有价值的新闻
        
        Args:
            news_list: 新闻列表，格式为 [{"title": "...", "source": "..."}]
            
        Returns:
            筛选后的新闻列表，每条新闻可能添加ai_score和ai_reason字段
        """
        if not self.is_available() or not self.config.get("ENABLE_NEWS_ANALYSIS"):
            return news_list

        filter_prompt = self.config.get("NEWS_FILTER_PROMPT", "")
        if not filter_prompt:
            return news_list

        try:
            print("开始使用AI分析新闻...")
            
            # 构建新闻列表文本
            news_text = "\n".join([
                f"{i+1}. [{news.get('source', '未知')}] {news.get('title', '')}"
                for i, news in enumerate(news_list)
            ])

            system_prompt = f"{filter_prompt}\n\n请以JSON格式返回结果，格式为：\n{{\n  \"results\": [\n    {{\"index\": 0, \"should_keep\": true, \"score\": 0.8, \"reason\": \"原因说明\"}}\n  ]\n}}\n\n要求：\n- index 从0开始\n- score 0-1之间，越高越有价值\n- should_keep 为true表示保留"

            user_prompt = f"请分析以下新闻：\n\n{news_text}"

            result = self.current_client.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=system_prompt
            )

            # 如果主API失败，尝试切换到备用API
            if not result and self._try_switch_backup():
                print("重试使用备用API分析新闻...")
                result = self.current_client.chat(
                    messages=[{"role": "user", "content": user_prompt}],
                    system_prompt=system_prompt
                )

            if not result:
                print("AI分析失败，使用原始新闻列表")
                return news_list

            # 解析JSON结果
            import json
            try:
                # 尝试提取JSON部分
                json_start = result.find("{")
                json_end = result.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = result[json_start:json_end]
                    analysis_result = json.loads(json_str)
                    
                    # 应用筛选结果
                    filtered_news = []
                    for i, news in enumerate(news_list):
                        news_copy = news.copy()
                        for res in analysis_result.get("results", []):
                            if res.get("index") == i:
                                news_copy["ai_score"] = res.get("score", 0)
                                news_copy["ai_reason"] = res.get("reason", "")
                                if res.get("should_keep", True):
                                    filtered_news.append(news_copy)
                                break
                        else:
                            filtered_news.append(news_copy)
                    
                    print(f"AI分析完成，筛选后保留 {len(filtered_news)}/{len(news_list)} 条新闻")
                    return filtered_news
                    
            except json.JSONDecodeError as e:
                print(f"解析AI响应失败: {e}")
                print(f"AI响应内容: {result}")
            
        except Exception as e:
            print(f"AI新闻分析异常: {e}")
        
        return news_list

    def generate_copywriting(self, news_list: List[Dict]) -> Optional[str]:
        """
        为新闻生成文案
        
        Args:
            news_list: 新闻列表
            
        Returns:
            生成的文案，失败返回None
        """
        if not self.is_available() or not self.config.get("ENABLE_COPYWRITING"):
            return None

        copywriting_prompt = self.config.get("COPYWRITING_PROMPT", "")
        if not copywriting_prompt:
            return None

        try:
            print("开始使用AI生成文案...")
            
            # 构建新闻内容
            news_text = "\n".join([
                f"- [{news.get('source', '未知')}] {news.get('title', '')}"
                for news in news_list[:10]  # 限制数量避免token过多
            ])

            user_prompt = f"{copywriting_prompt}\n\n新闻内容：\n{news_text}"

            result = self.current_client.chat(
                messages=[{"role": "user", "content": user_prompt}]
            )

            # 如果主API失败，尝试切换到备用API
            if not result and self._try_switch_backup():
                print("重试使用备用API生成文案...")
                result = self.current_client.chat(
                    messages=[{"role": "user", "content": user_prompt}]
                )

            if result:
                print("AI文案生成完成")
                return result
            else:
                print("AI文案生成失败")
                return None

        except Exception as e:
            print(f"AI文案生成异常: {e}")
            return None


# 初始化AI分析器
AI_ANALYZER = AINewsAnalyzer()


# === 小红书文案生成器 ===
class XiaohongshuContentGenerator:
    """小红书文案生成器 - 专为18-34岁高知女性内容生成文案"""

    @staticmethod
    def build_xiaohongshu_prompt(news_list: List[Dict]) -> str:
        """构建小红书文案生成的详细prompt"""
        
        # 格式化新闻内容，包含URL
        news_content = "\n".join([
            f"【{news.get('source', '未知来源')}】{news.get('title', '')}" +
            (f" ({news.get('mobile_url', '') or news.get('url', '')})" 
             if (news.get('mobile_url', '') or news.get('url', '')) 
             else "")
            for news in news_list[:15]  # 限制新闻数量避免token过长
        ])

        prompt = f"""你是一个擅长写小红书内容的资深文案，获得过金瞳奖。请按照以下五部分结构，为我生成一篇完整的内容。

【目标受众】
18-34岁、身处一二线城市的高知女性

【内容方向】
分析新闻内容，找出最贴合的方向（至少用到一个方向即可，不需要用上全部方向）：
- 智商税与避雷
- 塌房与翻车
- 消费降级与平替战争
- 情绪价值与悦己
- 平台算法与信息茧房

【新闻内容】
{news_content}

---

【第一部分：封面图文案】
请生成3-5个封面文案选项。
要求：
- 每条控制在15字以内
- 使用至少两种技法：数字冲击、结果前置、悬念留白、情绪标签、对比反差
- 不要完整句子，用关键词加情绪词
示例："3个信号❗️你的同事正在暗中排挤你"、"90%的女生不知道👀这5个雷区"

【第二部分：标题】
请生成3-5个标题选项。
要求：
- 字数控制在20字以内
- 暗合热搜词但不硬蹭
- 使用以下技法之一：直接嵌入热搜词、同义替换热搜词、情绪嫁接
- 保持客观中立，不要站队拉踩
- 结构建议：热点关键词+独特角度+情绪钩子
示例："全网都在讨论的XX，我想说一个女生视角的真相"

【第三部分：正文】
请生成正文四层内容，总字数800字左右，每层约200字。

第一层：时效加事实简述（约200字）
- 一句话交代事件背景
- 用自己的话重新组织核心事实
- 语气克制，只陈述事实不加判断
模板："这几天XX话题冲上了热搜，起因是…目前已知的情况是…"

第二层：分层拆解（约200字）
- 把事件拆成2-3个维度
- 每个维度一句话说清楚
- 拆解逻辑：从表面到本质、从个体到系统、或从现象到后果
- 每个拆解点要有信息增量
模板："这件事我们可以分两层来看。第一层是…第二层是…"

第三层：平民视角拉近距离（约200字）
- 用"我们"代替"你们"
- 用具体生活场景代替抽象概念
- 语气放松，可以有一点自嘲或共情
- 不要过度煽情
模板："其实对于我们普通人来说，这件事带来的最大启发不是…而是…"

第四层：客观措辞加中立语气（约200字）
- 不判断对错，只分析利弊
- 不说"这是对的错的"，说"这提醒了我们"或"这值得思考"
- 角度偏向女性：安全感、情绪价值、关系经营、自我成长
- 避免绝对化表述，少用"一定"、"绝对"、"所有"，多用"可能"、"往往"、"一定程度上"
- 避免敏感词，不直接批评具体人或机构
模板："从女生的角度来看，这件事给我们的提醒是…"

【第四部分：末尾固定人设标签】
请生成一段50字以内的固定结尾。
结构：身份+价值主张+行动召唤
与正文之间用"——"分隔
示例："——我是小雷达，专注分享女生视角的生活洞察。不制造焦虑，只提供思考角度。下期见。"

【第五部分：首评钩子】
请生成2-3个首评选项。
选择以下类型：
- 补充信息型：补充正文因风控没写的细节
- 互动提问型：抛出开放但具体的问题
- 价值延续型：引导去主页或等下一篇
示例："补充一个细节：据说…你们怎么看？"

---

【输出格式要求】
请严格按照以下JSON格式输出，不要包含任何额外说明文字：
{{
    "cover_options": ["封面1", "封面2", "封面3"],
    "title_options": ["标题1", "标题2", "标题3"],
    "body": "完整正文内容（四层合并）",
    "ending": "末尾人设标签",
    "first_comment_options": ["首评1", "首评2"]
}}
"""
        return prompt

    @classmethod
    def generate_full_content(cls, news_list: List[Dict]) -> Dict:
        """生成完整的小红书文案内容（调用AI）"""
        
        # 检查AI是否可用
        if not AI_ANALYZER.is_available() or not CONFIG["ALIYUN_QWEN"].get("ENABLE_COPYWRITING"):
            print("AI功能未启用，返回示例文案")
            result = {
                "cover_options": ["5个雷区💣女生必看", "智商税收割机⚠️", "塌房预警🚨避坑指南"],
                "title_options": ["关于智商税，我想说点实话", "消费降级时代的生存法则"],
                "body": "这几天关于消费陷阱的话题又热了起来。起因是多个品牌接连被曝出虚假宣传，从美妆到保健品，从网红店铺到直播间，各种'智商税'产品接连被扒。目前已知的情况是，多家媒体曝光了一系列产品，涉及夸大宣传、价格虚高、质量问题，消费者维权困难等。\n\n这件事我们可以分三层来看。第一层是信息不对称，商家利用信息差制造焦虑；第二层是情绪营销，让我们为情绪价值买单；第三层是平台算法，让我们陷入消费主义陷阱。\n\n其实对于我们普通人来说，这件事带来的最大启发不是省了多少钱，而是如何学会辨别信息的能力。说实话，我看到这些新闻的第一反应也是'啊，我也买过！'但后来我想了想，与其埋怨商家，不如反思自己的消费习惯。我们这代人太容易被情绪价值绑架了，总想通过消费来证明自己。\n\n从女生的角度来看，这件事给我们的提醒是，真正的安全感从来不是买了多少东西，而是内心的平静和自我认知。无论事件的真相如何，有一点是可以确定的：我们需要学会独立思考，不被算法和情绪左右。消费主义可能让我们以为买买买就能获得快乐，但真正的悦己，是找到适合自己的生活方式，学会甄别信息，不被别人的价值观绑架。",
                "ending": "——我是小雷达，专注分享女生视角的生活洞察。不制造焦虑，只提供思考角度。下期见。",
                "first_comment_options": ["你们有没有踩过类似的智商税？评论区聊聊！", "想看更多避坑内容的可以关注我！"]
            }
        else:
            try:
                print("开始用AI生成小红书文案...")
                
                prompt = cls.build_xiaohongshu_prompt(news_list)
                
                response = AI_ANALYZER.current_client.chat(
                    messages=[{"role": "user", "content": prompt}]
                )

                # 如果主API失败，尝试切换到备用API
                if not response and AI_ANALYZER._try_switch_backup():
                    print("重试使用备用API生成小红书文案...")
                    response = AI_ANALYZER.current_client.chat(
                        messages=[{"role": "user", "content": prompt}]
                    )

                if response:
                    # 解析AI返回的JSON
                    import json
                    try:
                        # 尝试从响应中提取JSON
                        json_start = response.find("{")
                        json_end = response.rfind("}") + 1
                        if json_start >= 0 and json_end > json_start:
                            json_str = response[json_start:json_end]
                            result = json.loads(json_str)
                            print("小红书文案AI生成完成")
                    except json.JSONDecodeError as e:
                        print(f"解析AI响应失败: {e}")
                        print(f"AI响应: {response}")
                        # 如果解析失败，返回默认内容
                        result = {
                            "cover_options": ["5个雷区💣女生必看", "智商税收割机⚠️"],
                            "title_options": ["关于智商税，我想说点实话"],
                            "body": "这几天关于消费陷阱的话题又热了起来。建议关注产品质量，理性消费。",
                            "ending": "——我是小雷达，专注分享女生视角的生活洞察。",
                            "first_comment_options": ["评论区聊聊你的看法！"]
                        }
                else:
                    # 如果AI没有返回，返回默认内容
                    result = {
                        "cover_options": ["5个雷区💣女生必看", "智商税收割机⚠️"],
                        "title_options": ["关于智商税，我想说点实话"],
                        "body": "这几天关于消费陷阱的话题又热了起来。建议关注产品质量，理性消费。",
                        "ending": "——我是小雷达，专注分享女生视角的生活洞察。",
                        "first_comment_options": ["评论区聊聊你的看法！"]
                    }

            except Exception as e:
                print(f"小红书文案生成异常: {e}")
                result = {
                    "cover_options": ["5个雷区💣女生必看"],
                    "title_options": ["关于智商税，我想说点实话"],
                    "body": "这几天关于消费陷阱的话题又热了起来。",
                    "ending": "——我是小雷达，下期见。",
                    "first_comment_options": ["评论区聊聊！"]
                }
        
        # 在结果中添加新闻链接列表
        news_links = []
        for news in news_list:
            title = news.get('title', '')
            url = news.get('mobile_url', '') or news.get('url', '')
            if title and url:
                news_links.append({
                    "title": title,
                    "url": url
                })
        if news_links:
            result["news_links"] = news_links
        
        return result


# === 工具函数 ===
def get_beijing_time():
    """获取北京时间"""
    return datetime.now(pytz.timezone("Asia/Shanghai"))


def format_date_folder():
    """格式化日期文件夹"""
    return get_beijing_time().strftime("%Y年%m月%d日")


def format_time_filename():
    """格式化时间文件名"""
    return get_beijing_time().strftime("%H时%M分")


def clean_title(title: str) -> str:
    """清理标题中的特殊字符"""
    if not isinstance(title, str):
        title = str(title)
    cleaned_title = title.replace("\n", " ").replace("\r", " ")
    cleaned_title = re.sub(r"\s+", " ", cleaned_title)
    cleaned_title = cleaned_title.strip()
    return cleaned_title


def ensure_directory_exists(directory: str):
    """确保目录存在"""
    Path(directory).mkdir(parents=True, exist_ok=True)


def get_output_path(subfolder: str, filename: str) -> str:
    """获取输出路径"""
    date_folder = format_date_folder()
    output_dir = Path("output") / date_folder / subfolder
    ensure_directory_exists(str(output_dir))
    return str(output_dir / filename)


def check_version_update(
        current_version: str, version_url: str, proxy_url: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """检查版本更新"""
    try:
        proxies = None
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/plain, */*",
            "Cache-Control": "no-cache",
        }

        response = requests.get(
            version_url, proxies=proxies, headers=headers, timeout=10
        )
        response.raise_for_status()

        remote_version = response.text.strip()
        print(f"当前版本: {current_version}, 远程版本: {remote_version}")

        # 比较版本
        def parse_version(version_str):
            try:
                parts = version_str.strip().split(".")
                if len(parts) != 3:
                    raise ValueError("版本号格式不正确")
                return int(parts[0]), int(parts[1]), int(parts[2])
            except:
                return 0, 0, 0

        current_tuple = parse_version(current_version)
        remote_tuple = parse_version(remote_version)

        need_update = current_tuple < remote_tuple
        return need_update, remote_version if need_update else None

    except Exception as e:
        print(f"版本检查失败: {e}")
        return False, None


def is_first_crawl_today() -> bool:
    """检测是否是当天第一次爬取"""
    date_folder = format_date_folder()
    txt_dir = Path("output") / date_folder / "txt"

    if not txt_dir.exists():
        return True

    files = sorted([f for f in txt_dir.iterdir() if f.suffix == ".txt"])
    return len(files) <= 1


def html_escape(text: str) -> str:
    """HTML转义"""
    if not isinstance(text, str):
        text = str(text)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


# === 推送记录管理 ===
class PushRecordManager:
    """推送记录管理器"""

    def __init__(self):
        self.record_dir = Path("output") / ".push_records"
        self.ensure_record_dir()
        self.cleanup_old_records()

    def ensure_record_dir(self):
        """确保记录目录存在"""
        self.record_dir.mkdir(parents=True, exist_ok=True)

    def get_today_record_file(self) -> Path:
        """获取今天的记录文件路径"""
        today = get_beijing_time().strftime("%Y%m%d")
        return self.record_dir / f"push_record_{today}.json"

    def cleanup_old_records(self):
        """清理过期的推送记录"""
        retention_days = CONFIG["SILENT_PUSH"]["RECORD_RETENTION_DAYS"]
        current_time = get_beijing_time()

        for record_file in self.record_dir.glob("push_record_*.json"):
            try:
                date_str = record_file.stem.replace("push_record_", "")
                file_date = datetime.strptime(date_str, "%Y%m%d")
                file_date = pytz.timezone("Asia/Shanghai").localize(file_date)

                if (current_time - file_date).days > retention_days:
                    record_file.unlink()
                    print(f"清理过期推送记录: {record_file.name}")
            except Exception as e:
                print(f"清理记录文件失败 {record_file}: {e}")

    def has_pushed_today(self) -> bool:
        """检查今天是否已经推送过"""
        record_file = self.get_today_record_file()

        if not record_file.exists():
            return False

        try:
            with open(record_file, "r", encoding="utf-8") as f:
                record = json.load(f)
            return record.get("pushed", False)
        except Exception as e:
            print(f"读取推送记录失败: {e}")
            return False

    def record_push(self, report_type: str):
        """记录推送"""
        record_file = self.get_today_record_file()
        now = get_beijing_time()

        record = {
            "pushed": True,
            "push_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "report_type": report_type,
        }

        try:
            with open(record_file, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            print(f"推送记录已保存: {report_type} at {now.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"保存推送记录失败: {e}")

    def is_in_time_range(self, start_time: str, end_time: str) -> bool:
        """检查当前时间是否在指定时间范围内"""
        now = get_beijing_time()
        current_time = now.strftime("%H:%M")
        return start_time <= current_time <= end_time


# === 数据获取 ===
class DataFetcher:
    """数据获取器"""

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url

    def fetch_data(
            self,
            id_info: Union[str, Tuple[str, str]],
            max_retries: int = 2,
            min_retry_wait: int = 3,
            max_retry_wait: int = 5,
    ) -> Tuple[Optional[str], str, str]:
        """获取指定ID数据，支持重试"""
        if isinstance(id_info, tuple):
            id_value, alias = id_info
        else:
            id_value = id_info
            alias = id_value

        url = f"https://newsnow.busiyi.world/api/s?id={id_value}&latest"

        proxies = None
        if self.proxy_url:
            proxies = {"http": self.proxy_url, "https": self.proxy_url}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
        }

        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(
                    url, proxies=proxies, headers=headers, timeout=10
                )
                response.raise_for_status()

                data_text = response.text
                data_json = json.loads(data_text)

                status = data_json.get("status", "未知")
                if status not in ["success", "cache"]:
                    raise ValueError(f"响应状态异常: {status}")

                status_info = "最新数据" if status == "success" else "缓存数据"
                print(f"获取 {id_value} 成功（{status_info}）")
                return data_text, id_value, alias

            except Exception as e:
                retries += 1
                if retries <= max_retries:
                    base_wait = random.uniform(min_retry_wait, max_retry_wait)
                    additional_wait = (retries - 1) * random.uniform(1, 2)
                    wait_time = base_wait + additional_wait
                    print(f"请求 {id_value} 失败: {e}. {wait_time:.2f}秒后重试...")
                    time.sleep(wait_time)
                else:
                    print(f"请求 {id_value} 失败: {e}")
                    return None, id_value, alias
        return None, id_value, alias

    def crawl_websites(
            self,
            ids_list: List[Union[str, Tuple[str, str]]],
            request_interval: int = CONFIG["REQUEST_INTERVAL"],
    ) -> Tuple[Dict, Dict, List]:
        """爬取多个网站数据"""
        results = {}
        id_to_name = {}
        failed_ids = []

        for i, id_info in enumerate(ids_list):
            if isinstance(id_info, tuple):
                id_value, name = id_info
            else:
                id_value = id_info
                name = id_value

            id_to_name[id_value] = name
            response, _, _ = self.fetch_data(id_info)

            if response:
                try:
                    data = json.loads(response)
                    results[id_value] = {}
                    for index, item in enumerate(data.get("items", []), 1):
                        title = item["title"]
                        url = item.get("url", "")
                        mobile_url = item.get("mobileUrl", "")

                        if title in results[id_value]:
                            results[id_value][title]["ranks"].append(index)
                        else:
                            results[id_value][title] = {
                                "ranks": [index],
                                "url": url,
                                "mobileUrl": mobile_url,
                            }
                except json.JSONDecodeError:
                    print(f"解析 {id_value} 响应失败")
                    failed_ids.append(id_value)
                except Exception as e:
                    print(f"处理 {id_value} 数据出错: {e}")
                    failed_ids.append(id_value)
            else:
                failed_ids.append(id_value)

            if i < len(ids_list) - 1:
                actual_interval = request_interval + random.randint(-10, 20)
                actual_interval = max(50, actual_interval)
                time.sleep(actual_interval / 1000)

        print(f"成功: {list(results.keys())}, 失败: {failed_ids}")
        return results, id_to_name, failed_ids


# === 数据处理 ===
def save_titles_to_file(results: Dict, id_to_name: Dict, failed_ids: List) -> str:
    """保存标题到文件"""
    file_path = get_output_path("txt", f"{format_time_filename()}.txt")

    with open(file_path, "w", encoding="utf-8") as f:
        for id_value, title_data in results.items():
            # id | name 或 id
            name = id_to_name.get(id_value)
            if name and name != id_value:
                f.write(f"{id_value} | {name}\n")
            else:
                f.write(f"{id_value}\n")

            # 按排名排序标题
            sorted_titles = []
            for title, info in title_data.items():
                cleaned_title = clean_title(title)
                if isinstance(info, dict):
                    ranks = info.get("ranks", [])
                    url = info.get("url", "")
                    mobile_url = info.get("mobileUrl", "")
                else:
                    ranks = info if isinstance(info, list) else []
                    url = ""
                    mobile_url = ""

                rank = ranks[0] if ranks else 1
                sorted_titles.append((rank, cleaned_title, url, mobile_url))

            sorted_titles.sort(key=lambda x: x[0])

            for rank, cleaned_title, url, mobile_url in sorted_titles:
                line = f"{rank}. {cleaned_title}"

                if url:
                    line += f" [URL:{url}]"
                if mobile_url:
                    line += f" [MOBILE:{mobile_url}]"
                f.write(line + "\n")

            f.write("\n")

        if failed_ids:
            f.write("==== 以下ID请求失败 ====\n")
            for id_value in failed_ids:
                f.write(f"{id_value}\n")

    return file_path


def load_frequency_words(
        frequency_file: Optional[str] = None,
) -> Tuple[List[Dict], List[str]]:
    """加载频率词配置"""
    if frequency_file is None:
        frequency_file = os.environ.get(
            "FREQUENCY_WORDS_PATH", "config/frequency_words.txt"
        )

    frequency_path = Path(frequency_file)
    if not frequency_path.exists():
        raise FileNotFoundError(f"频率词文件 {frequency_file} 不存在")

    with open(frequency_path, "r", encoding="utf-8") as f:
        content = f.read()

    word_groups = [group.strip() for group in content.split("\n\n") if group.strip()]

    processed_groups = []
    filter_words = []

    for group in word_groups:
        words = [word.strip() for word in group.split("\n") if word.strip()]

        group_required_words = []
        group_normal_words = []
        group_filter_words = []

        for word in words:
            if word.startswith("!"):
                filter_words.append(word[1:])
                group_filter_words.append(word[1:])
            elif word.startswith("+"):
                group_required_words.append(word[1:])
            else:
                group_normal_words.append(word)

        if group_required_words or group_normal_words:
            if group_normal_words:
                group_key = " ".join(group_normal_words)
            else:
                group_key = " ".join(group_required_words)

            processed_groups.append(
                {
                    "required": group_required_words,
                    "normal": group_normal_words,
                    "group_key": group_key,
                }
            )

    return processed_groups, filter_words


def parse_file_titles(file_path: Path) -> Tuple[Dict, Dict]:
    """解析单个txt文件的标题数据，返回(titles_by_id, id_to_name)"""
    titles_by_id = {}
    id_to_name = {}

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        sections = content.split("\n\n")

        for section in sections:
            if not section.strip() or "==== 以下ID请求失败 ====" in section:
                continue

            lines = section.strip().split("\n")
            if len(lines) < 2:
                continue

            # id | name 或 id
            header_line = lines[0].strip()
            if " | " in header_line:
                parts = header_line.split(" | ", 1)
                source_id = parts[0].strip()
                name = parts[1].strip()
                id_to_name[source_id] = name
            else:
                source_id = header_line
                id_to_name[source_id] = source_id

            titles_by_id[source_id] = {}

            for line in lines[1:]:
                if line.strip():
                    try:
                        title_part = line.strip()
                        rank = None

                        # 提取排名
                        if ". " in title_part and title_part.split(". ")[0].isdigit():
                            rank_str, title_part = title_part.split(". ", 1)
                            rank = int(rank_str)

                        # 提取 MOBILE URL
                        mobile_url = ""
                        if " [MOBILE:" in title_part:
                            title_part, mobile_part = title_part.rsplit(" [MOBILE:", 1)
                            if mobile_part.endswith("]"):
                                mobile_url = mobile_part[:-1]

                        # 提取 URL
                        url = ""
                        if " [URL:" in title_part:
                            title_part, url_part = title_part.rsplit(" [URL:", 1)
                            if url_part.endswith("]"):
                                url = url_part[:-1]

                        title = clean_title(title_part.strip())
                        ranks = [rank] if rank is not None else [1]

                        titles_by_id[source_id][title] = {
                            "ranks": ranks,
                            "url": url,
                            "mobileUrl": mobile_url,
                        }

                    except Exception as e:
                        print(f"解析标题行出错: {line}, 错误: {e}")

    return titles_by_id, id_to_name


def read_all_today_titles(
        current_platform_ids: Optional[List[str]] = None,
) -> Tuple[Dict, Dict, Dict]:
    """读取当天所有标题文件，支持按当前监控平台过滤"""
    date_folder = format_date_folder()
    txt_dir = Path("output") / date_folder / "txt"

    if not txt_dir.exists():
        return {}, {}, {}

    all_results = {}
    final_id_to_name = {}
    title_info = {}

    files = sorted([f for f in txt_dir.iterdir() if f.suffix == ".txt"])

    for file_path in files:
        time_info = file_path.stem

        titles_by_id, file_id_to_name = parse_file_titles(file_path)

        if current_platform_ids is not None:
            filtered_titles_by_id = {}
            filtered_id_to_name = {}

            for source_id, title_data in titles_by_id.items():
                if source_id in current_platform_ids:
                    filtered_titles_by_id[source_id] = title_data
                    if source_id in file_id_to_name:
                        filtered_id_to_name[source_id] = file_id_to_name[source_id]

            titles_by_id = filtered_titles_by_id
            file_id_to_name = filtered_id_to_name

        final_id_to_name.update(file_id_to_name)

        for source_id, title_data in titles_by_id.items():
            process_source_data(
                source_id, title_data, time_info, all_results, title_info
            )

    return all_results, final_id_to_name, title_info


def process_source_data(
        source_id: str,
        title_data: Dict,
        time_info: str,
        all_results: Dict,
        title_info: Dict,
) -> None:
    """处理来源数据，合并重复标题"""
    if source_id not in all_results:
        all_results[source_id] = title_data

        if source_id not in title_info:
            title_info[source_id] = {}

        for title, data in title_data.items():
            ranks = data.get("ranks", [])
            url = data.get("url", "")
            mobile_url = data.get("mobileUrl", "")

            title_info[source_id][title] = {
                "first_time": time_info,
                "last_time": time_info,
                "count": 1,
                "ranks": ranks,
                "url": url,
                "mobileUrl": mobile_url,
            }
    else:
        for title, data in title_data.items():
            ranks = data.get("ranks", [])
            url = data.get("url", "")
            mobile_url = data.get("mobileUrl", "")

            if title not in all_results[source_id]:
                all_results[source_id][title] = {
                    "ranks": ranks,
                    "url": url,
                    "mobileUrl": mobile_url,
                }
                title_info[source_id][title] = {
                    "first_time": time_info,
                    "last_time": time_info,
                    "count": 1,
                    "ranks": ranks,
                    "url": url,
                    "mobileUrl": mobile_url,
                }
            else:
                existing_data = all_results[source_id][title]
                existing_ranks = existing_data.get("ranks", [])
                existing_url = existing_data.get("url", "")
                existing_mobile_url = existing_data.get("mobileUrl", "")

                merged_ranks = existing_ranks.copy()
                for rank in ranks:
                    if rank not in merged_ranks:
                        merged_ranks.append(rank)

                all_results[source_id][title] = {
                    "ranks": merged_ranks,
                    "url": existing_url or url,
                    "mobileUrl": existing_mobile_url or mobile_url,
                }

                title_info[source_id][title]["last_time"] = time_info
                title_info[source_id][title]["ranks"] = merged_ranks
                title_info[source_id][title]["count"] += 1
                if not title_info[source_id][title].get("url"):
                    title_info[source_id][title]["url"] = url
                if not title_info[source_id][title].get("mobileUrl"):
                    title_info[source_id][title]["mobileUrl"] = mobile_url


def detect_latest_new_titles(current_platform_ids: Optional[List[str]] = None) -> Dict:
    """检测当日最新批次的新增标题，支持按当前监控平台过滤"""
    date_folder = format_date_folder()
    txt_dir = Path("output") / date_folder / "txt"

    if not txt_dir.exists():
        return {}

    files = sorted([f for f in txt_dir.iterdir() if f.suffix == ".txt"])
    if len(files) < 2:
        return {}

    # 解析最新文件
    latest_file = files[-1]
    latest_titles, _ = parse_file_titles(latest_file)

    # 如果指定了当前平台列表，过滤最新文件数据
    if current_platform_ids is not None:
        filtered_latest_titles = {}
        for source_id, title_data in latest_titles.items():
            if source_id in current_platform_ids:
                filtered_latest_titles[source_id] = title_data
        latest_titles = filtered_latest_titles

    # 汇总历史标题（按平台过滤）
    historical_titles = {}
    for file_path in files[:-1]:
        historical_data, _ = parse_file_titles(file_path)

        # 过滤历史数据
        if current_platform_ids is not None:
            filtered_historical_data = {}
            for source_id, title_data in historical_data.items():
                if source_id in current_platform_ids:
                    filtered_historical_data[source_id] = title_data
            historical_data = filtered_historical_data

        for source_id, titles_data in historical_data.items():
            if source_id not in historical_titles:
                historical_titles[source_id] = set()
            for title in titles_data.keys():
                historical_titles[source_id].add(title)

    # 找出新增标题
    new_titles = {}
    for source_id, latest_source_titles in latest_titles.items():
        historical_set = historical_titles.get(source_id, set())
        source_new_titles = {}

        for title, title_data in latest_source_titles.items():
            if title not in historical_set:
                source_new_titles[title] = title_data

        if source_new_titles:
            new_titles[source_id] = source_new_titles

    return new_titles


# === 统计和分析 ===
def calculate_news_weight(
        title_data: Dict, rank_threshold: int = CONFIG["RANK_THRESHOLD"]
) -> float:
    """计算新闻权重，用于排序"""
    ranks = title_data.get("ranks", [])
    if not ranks:
        return 0.0

    count = title_data.get("count", len(ranks))
    weight_config = CONFIG["WEIGHT_CONFIG"]

    # 排名权重：Σ(11 - min(rank, 10)) / 出现次数
    rank_scores = []
    for rank in ranks:
        score = 11 - min(rank, 10)
        rank_scores.append(score)

    rank_weight = sum(rank_scores) / len(ranks) if ranks else 0

    # 频次权重：min(出现次数, 10) × 10
    frequency_weight = min(count, 10) * 10

    # 热度加成：高排名次数 / 总出现次数 × 100
    high_rank_count = sum(1 for rank in ranks if rank <= rank_threshold)
    hotness_ratio = high_rank_count / len(ranks) if ranks else 0
    hotness_weight = hotness_ratio * 100

    total_weight = (
            rank_weight * weight_config["RANK_WEIGHT"]
            + frequency_weight * weight_config["FREQUENCY_WEIGHT"]
            + hotness_weight * weight_config["HOTNESS_WEIGHT"]
    )

    return total_weight


def matches_word_groups(
        title: str, word_groups: List[Dict], filter_words: List[str]
) -> bool:
    """检查标题是否匹配词组规则"""
    # 如果没有配置词组，则匹配所有标题（支持显示全部新闻）
    if not word_groups:
        return True

    title_lower = title.lower()

    # 过滤词检查
    if any(filter_word.lower() in title_lower for filter_word in filter_words):
        return False

    # 词组匹配检查
    for group in word_groups:
        required_words = group["required"]
        normal_words = group["normal"]

        # 必须词检查
        if required_words:
            all_required_present = all(
                req_word.lower() in title_lower for req_word in required_words
            )
            if not all_required_present:
                continue

        # 普通词检查
        if normal_words:
            any_normal_present = any(
                normal_word.lower() in title_lower for normal_word in normal_words
            )
            if not any_normal_present:
                continue

        return True

    return False


def format_time_display(first_time: str, last_time: str) -> str:
    """格式化时间显示"""
    if not first_time:
        return ""
    if first_time == last_time or not last_time:
        return first_time
    else:
        return f"[{first_time} ~ {last_time}]"


def format_rank_display(ranks: List[int], rank_threshold: int, format_type: str) -> str:
    """统一的排名格式化方法"""
    if not ranks:
        return ""

    unique_ranks = sorted(set(ranks))
    min_rank = unique_ranks[0]
    max_rank = unique_ranks[-1]

    if format_type == "html":
        highlight_start = "<font color='red'><strong>"
        highlight_end = "</strong></font>"
    elif format_type == "feishu":
        highlight_start = "<font color='red'>**"
        highlight_end = "**</font>"
    elif format_type == "dingtalk":
        highlight_start = "**"
        highlight_end = "**"
    elif format_type == "wework":
        highlight_start = "**"
        highlight_end = "**"
    elif format_type == "telegram":
        highlight_start = "<b>"
        highlight_end = "</b>"
    else:
        highlight_start = "**"
        highlight_end = "**"

    if min_rank <= rank_threshold:
        if min_rank == max_rank:
            return f"{highlight_start}[{min_rank}]{highlight_end}"
        else:
            return f"{highlight_start}[{min_rank} - {max_rank}]{highlight_end}"
    else:
        if min_rank == max_rank:
            return f"[{min_rank}]"
        else:
            return f"[{min_rank} - {max_rank}]"


def count_word_frequency(
        results: Dict,
        word_groups: List[Dict],
        filter_words: List[str],
        id_to_name: Dict,
        title_info: Optional[Dict] = None,
        rank_threshold: int = CONFIG["RANK_THRESHOLD"],
        new_titles: Optional[Dict] = None,
        mode: str = "daily",
) -> Tuple[List[Dict], int]:
    """统计词频，支持必须词、频率词、过滤词，并标记新增标题"""

    ai_copywriting = None

    # 如果没有配置词组，创建一个包含所有新闻的虚拟词组
    if not word_groups:
        print("频率词配置为空，将显示所有新闻")
        word_groups = [{"required": [], "normal": [], "group_key": "全部新闻"}]
        filter_words = []  # 清空过滤词，显示所有新闻

    is_first_today = is_first_crawl_today()

    # 确定处理的数据源和新增标记逻辑
    if mode == "incremental":
        if is_first_today:
            # 增量模式 + 当天第一次：处理所有新闻，都标记为新增
            results_to_process = results
            all_news_are_new = True
        else:
            # 增量模式 + 当天非第一次：只处理新增的新闻
            results_to_process = new_titles if new_titles else {}
            all_news_are_new = True
    elif mode == "current":
        # current 模式：只处理当前时间批次的新闻，但统计信息来自全部历史
        if title_info:
            latest_time = None
            for source_titles in title_info.values():
                for title_data in source_titles.values():
                    last_time = title_data.get("last_time", "")
                    if last_time:
                        if latest_time is None or last_time > latest_time:
                            latest_time = last_time

            # 只处理 last_time 等于最新时间的新闻
            if latest_time:
                results_to_process = {}
                for source_id, source_titles in results.items():
                    if source_id in title_info:
                        filtered_titles = {}
                        for title, title_data in source_titles.items():
                            if title in title_info[source_id]:
                                info = title_info[source_id][title]
                                if info.get("last_time") == latest_time:
                                    filtered_titles[title] = title_data
                        if filtered_titles:
                            results_to_process[source_id] = filtered_titles

                print(
                    f"当前榜单模式：最新时间 {latest_time}，筛选出 {sum(len(titles) for titles in results_to_process.values())} 条当前榜单新闻"
                )
            else:
                results_to_process = results
        else:
            results_to_process = results
        all_news_are_new = False
    else:
        # 当日汇总模式：处理所有新闻
        results_to_process = results
        all_news_are_new = False
        total_input_news = sum(len(titles) for titles in results.values())
        filter_status = (
            "全部显示"
            if len(word_groups) == 1 and word_groups[0]["group_key"] == "全部新闻"
            else "频率词过滤"
        )
        print(f"当日汇总模式：处理 {total_input_news} 条新闻，模式：{filter_status}")

    word_stats = {}
    total_titles = 0
    processed_titles = {}
    matched_new_count = 0
    filtered_news_list = []  # 词库匹配后的新闻列表（供 AI 分析使用）

    if title_info is None:
        title_info = {}
    if new_titles is None:
        new_titles = {}

    for group in word_groups:
        group_key = group["group_key"]
        word_stats[group_key] = {"count": 0, "titles": {}}

    for source_id, titles_data in results_to_process.items():
        total_titles += len(titles_data)

        if source_id not in processed_titles:
            processed_titles[source_id] = {}

        for title, title_data in titles_data.items():
            if title in processed_titles.get(source_id, {}):
                continue

            # 使用统一的匹配逻辑
            matches_frequency_words = matches_word_groups(
                title, word_groups, filter_words
            )

            if not matches_frequency_words:
                continue

            # 将词库匹配的新闻加入 AI 分析候选列表
            filtered_news_list.append({
                "title": title,
                "source": id_to_name.get(source_id, source_id),
                "source_id": source_id,
                "title_data": title_data
            })

            # 如果是增量模式或 current 模式第一次，统计匹配的新增新闻数量
            if (mode == "incremental" and all_news_are_new) or (
                    mode == "current" and is_first_today
            ):
                matched_new_count += 1

            source_ranks = title_data.get("ranks", [])
            source_url = title_data.get("url", "")
            source_mobile_url = title_data.get("mobileUrl", "")

            # 找到匹配的词组
            title_lower = title.lower()
            for group in word_groups:
                required_words = group["required"]
                normal_words = group["normal"]

                # 如果是"全部新闻"模式，所有标题都匹配第一个（唯一的）词组
                if len(word_groups) == 1 and word_groups[0]["group_key"] == "全部新闻":
                    group_key = group["group_key"]
                    word_stats[group_key]["count"] += 1
                    if source_id not in word_stats[group_key]["titles"]:
                        word_stats[group_key]["titles"][source_id] = []
                else:
                    # 原有的匹配逻辑
                    if required_words:
                        all_required_present = all(
                            req_word.lower() in title_lower
                            for req_word in required_words
                        )
                        if not all_required_present:
                            continue

                    if normal_words:
                        any_normal_present = any(
                            normal_word.lower() in title_lower
                            for normal_word in normal_words
                        )
                        if not any_normal_present:
                            continue

                    group_key = group["group_key"]
                    word_stats[group_key]["count"] += 1
                    if source_id not in word_stats[group_key]["titles"]:
                        word_stats[group_key]["titles"][source_id] = []

                first_time = ""
                last_time = ""
                count_info = 1
                ranks = source_ranks if source_ranks else []
                url = source_url
                mobile_url = source_mobile_url

                # 对于 current 模式，从历史统计信息中获取完整数据
                if (
                        mode == "current"
                        and title_info
                        and source_id in title_info
                        and title in title_info[source_id]
                ):
                    info = title_info[source_id][title]
                    first_time = info.get("first_time", "")
                    last_time = info.get("last_time", "")
                    count_info = info.get("count", 1)
                    if "ranks" in info and info["ranks"]:
                        ranks = info["ranks"]
                    url = info.get("url", source_url)
                    mobile_url = info.get("mobileUrl", source_mobile_url)
                elif (
                        title_info
                        and source_id in title_info
                        and title in title_info[source_id]
                ):
                    info = title_info[source_id][title]
                    first_time = info.get("first_time", "")
                    last_time = info.get("last_time", "")
                    count_info = info.get("count", 1)
                    if "ranks" in info and info["ranks"]:
                        ranks = info["ranks"]
                    url = info.get("url", source_url)
                    mobile_url = info.get("mobileUrl", source_mobile_url)

                if not ranks:
                    ranks = [99]

                time_display = format_time_display(first_time, last_time)

                source_name = id_to_name.get(source_id, source_id)

                # 判断是否为新增
                is_new = False
                if all_news_are_new:
                    # 增量模式下所有处理的新闻都是新增，或者当天第一次的所有新闻都是新增
                    is_new = True
                elif new_titles and source_id in new_titles:
                    # 检查是否在新增列表中
                    new_titles_for_source = new_titles[source_id]
                    is_new = title in new_titles_for_source

                word_stats[group_key]["titles"][source_id].append(
                    {
                        "title": title,
                        "source_name": source_name,
                        "first_time": first_time,
                        "last_time": last_time,
                        "time_display": time_display,
                        "count": count_info,
                        "ranks": ranks,
                        "rank_threshold": rank_threshold,
                        "url": url,
                        "mobileUrl": mobile_url,
                        "is_new": is_new,
                    }
                )

                if source_id not in processed_titles:
                    processed_titles[source_id] = {}
                processed_titles[source_id][title] = True

                break

    # 最后统一打印汇总信息
    if mode == "incremental":
        if is_first_today:
            total_input_news = sum(len(titles) for titles in results.values())
            filter_status = (
                "全部显示"
                if len(word_groups) == 1 and word_groups[0]["group_key"] == "全部新闻"
                else "频率词匹配"
            )
            print(
                f"增量模式：当天第一次爬取，{total_input_news} 条新闻中有 {matched_new_count} 条{filter_status}"
            )
        else:
            if new_titles:
                total_new_count = sum(len(titles) for titles in new_titles.values())
                filter_status = (
                    "全部显示"
                    if len(word_groups) == 1
                       and word_groups[0]["group_key"] == "全部新闻"
                    else "匹配频率词"
                )
                print(
                    f"增量模式：{total_new_count} 条新增新闻中，有 {matched_new_count} 条{filter_status}"
                )
                if matched_new_count == 0 and len(word_groups) > 1:
                    print("增量模式：没有新增新闻匹配频率词，将不会发送通知")
            else:
                print("增量模式：未检测到新增新闻")
    elif mode == "current":
        total_input_news = sum(len(titles) for titles in results_to_process.values())
        if is_first_today:
            filter_status = (
                "全部显示"
                if len(word_groups) == 1 and word_groups[0]["group_key"] == "全部新闻"
                else "频率词匹配"
            )
            print(
                f"当前榜单模式：当天第一次爬取，{total_input_news} 条当前榜单新闻中有 {matched_new_count} 条{filter_status}"
            )
        else:
            matched_count = sum(stat["count"] for stat in word_stats.values())
            filter_status = (
                "全部显示"
                if len(word_groups) == 1 and word_groups[0]["group_key"] == "全部新闻"
                else "频率词匹配"
            )
            print(
                f"当前榜单模式：{total_input_news} 条当前榜单新闻中有 {matched_count} 条{filter_status}"
            )

    # === 第二步：AI新闻分析精选（只分析词库匹配后的新闻） ===
    ai_analyzed_titles = None  # AI 精选后的标题集合（如启用了新闻分析）

    if AI_ANALYZER.is_available():
        # AI 新闻筛选（只处理词库匹配后的新闻，效率更高）
        if CONFIG["ALIYUN_QWEN"].get("ENABLE_NEWS_ANALYSIS"):
            if filtered_news_list:
                analyzed_news = AI_ANALYZER.analyze_news_batch(filtered_news_list)
                if analyzed_news:
                    # 记录 AI 精选的标题集合，用于后续过滤 word_stats
                    ai_analyzed_titles = {news.get("title") for news in analyzed_news}
                    # 更新 filtered_news_list 为 AI 精选结果，供文案生成使用
                    filtered_news_list = analyzed_news
                    print(
                        f"AI 新闻分析：词库匹配 {len(ai_analyzed_titles)} 条，经 AI 精选后保留"
                    )
            else:
                print("没有词库匹配的新闻，跳过 AI 新闻分析")

        # === 第三步：AI文案生成（使用词库筛选后的新闻，每条新闻生成独立文案） ===
        if CONFIG["ALIYUN_QWEN"].get("ENABLE_COPYWRITING"):
            if filtered_news_list:
                # 从匹配的新闻中最多选2条，各生成一条独立的小红书文案
                import random
                selected_news = random.sample(
                    filtered_news_list,
                    min(2, len(filtered_news_list))
                ) if len(filtered_news_list) >= 2 else filtered_news_list

                ai_copywriting_list = []
                for idx, news_item in enumerate(selected_news):
                    # 将 news_item 转换为 XiaohongshuContentGenerator 需要的格式
                    xhs_news = [{
                        "title": news_item.get("title", ""),
                        "source": news_item.get("source", ""),
                        "url": news_item.get("title_data", {}).get("url", ""),
                        "mobile_url": news_item.get("title_data", {}).get("mobileUrl", "")
                    }]
                    try:
                        print(f"开始为第 {idx+1} 条新闻生成小红书文案: {news_item.get('title', '')[:30]}...")
                        content = XiaohongshuContentGenerator.generate_full_content(xhs_news)
                        ai_copywriting_list.append({
                            "news_title": news_item.get("title", ""),
                            "news_source": news_item.get("source", ""),
                            "content": content
                        })
                    except Exception as e:
                        print(f"第 {idx+1} 条新闻文案生成失败: {e}")

                if ai_copywriting_list:
                    # 存储为列表格式，供后续处理
                    ai_copywriting = {"copies": ai_copywriting_list}
                    print(f"AI文案生成完成，共生成 {len(ai_copywriting_list)} 条")
            else:
                print("没有匹配frequency_words的新闻，跳过AI文案生成")

    stats = []
    for group_key, data in word_stats.items():
        all_titles = []
        for source_id, title_list in data["titles"].items():
            for t in title_list:
                # 如果启用了 AI 新闻分析，只保留 AI 精选后的标题
                if ai_analyzed_titles is not None:
                    if t["title"] in ai_analyzed_titles:
                        all_titles.append(t)
                else:
                    all_titles.append(t)

        # 如果启用了 AI 新闻分析且该词组下没有匹配的新闻，跳过
        if ai_analyzed_titles is not None and not all_titles:
            continue

        # 按权重排序
        sorted_titles = sorted(
            all_titles,
            key=lambda x: (
                -calculate_news_weight(x, rank_threshold),
                min(x["ranks"]) if x["ranks"] else 999,
                -x["count"],
            ),
        )

        stats.append(
            {
                "word": group_key,
                "count": len(sorted_titles),
                "titles": sorted_titles,
                "percentage": (
                    round(len(sorted_titles) / total_titles * 100, 2)
                    if total_titles > 0
                    else 0
                ),
            }
        )

    stats.sort(key=lambda x: x["count"], reverse=True)

    # 如果有AI生成的文案，添加到返回结果中
    if ai_copywriting:
        # 将文案添加到stats的第一个元素中，或者创建一个专门的条目
        if stats:
            stats[0]["ai_copywriting"] = ai_copywriting
        else:
            stats.append({
                "word": "AI文案",
                "count": 0,
                "titles": [],
                "percentage": 0,
                "ai_copywriting": ai_copywriting
            })

    return stats, total_titles


# === 小红书文案生成 ===
def generate_xiaohongshu_content(stats: List[Dict], failed_ids: Optional[List] = None) -> Optional[str]:
    """生成小红书文案并保存到文件"""
    # 检查是否启用小红书功能
    if not CONFIG["XIAOHONGSHU"]["ENABLED"]:
        print("小红书文案生成功能未启用，跳过")
        return None

    # 检查是否有预生成的 AI 文案（从 count_word_frequency 传入）
    ai_copywriting = None
    if stats and "ai_copywriting" in stats[0]:
        ai_copywriting = stats[0]["ai_copywriting"]

    if ai_copywriting and isinstance(ai_copywriting, dict) and "copies" in ai_copywriting:
        # 使用预生成的 AI 文案（多条）
        copies = ai_copywriting["copies"]
        output_text = "=" * 50 + "\n"
        output_text += "           📕 小红书文案生成\n"
        output_text += "=" * 50 + "\n\n"

        for idx, copy in enumerate(copies):
            content = copy.get("content", {})
            output_text += f"--- 第 {idx+1} 条新闻 ---\n"
            output_text += f"📰 {copy.get('news_title', '')}\n"
            output_text += f"📢 来源: {copy.get('news_source', '')}\n\n"

            output_text += "【第一部分：封面图文案】\n"
            for i, cover in enumerate(content.get("cover_options", []), 1):
                output_text += f"{i}. {cover}\n"
            output_text += "\n"

            output_text += "【第二部分：标题】\n"
            for i, title in enumerate(content.get("title_options", []), 1):
                output_text += f"{i}. {title}\n"
            output_text += "\n"

            output_text += "【第三部分：正文】\n"
            output_text += content.get("body", "") + "\n\n"

            output_text += "【第四部分：末尾固定人设标签】\n"
            output_text += content.get("ending", "") + "\n\n"

            output_text += "【第五部分：首评钩子】\n"
            for i, comment in enumerate(content.get("first_comment_options", []), 1):
                output_text += f"{i}. {comment}\n"
            output_text += "\n"

            # 添加新闻链接
            if content.get("news_links"):
                output_text += "【参考新闻链接】\n"
                for i, link in enumerate(content["news_links"], 1):
                    output_text += f"{i}. {link['title']}\n"
                    output_text += f"   {link['url']}\n"
                output_text += "\n"

            output_text += "\n"

    else:
        # 兜底：从 stats 收集新闻并重新生成文案
        news_list = []
        for stat in stats:
            for title_data in stat.get("titles", []):
                news_list.append({
                    "title": title_data.get("title", ""),
                    "source": title_data.get("source_name", ""),
                    "url": title_data.get("url", ""),
                    "mobile_url": title_data.get("mobile_url", "")
                })

        if not news_list:
            print("没有足够的新闻内容生成小红书文案")
            return None

        # 生成文案
        content = XiaohongshuContentGenerator.generate_full_content(news_list)

        # 格式化输出
        output_text = "=" * 50 + "\n"
        output_text += "           📕 小红书文案生成\n"
        output_text += "=" * 50 + "\n\n"

        output_text += "【第一部分：封面图文案】\n"
        for i, cover in enumerate(content["cover_options"], 1):
            output_text += f"{i}. {cover}\n"
        output_text += "\n"

        output_text += "【第二部分：标题】\n"
        for i, title in enumerate(content["title_options"], 1):
            output_text += f"{i}. {title}\n"
        output_text += "\n"

        output_text += "【第三部分：正文】\n"
        output_text += content["body"] + "\n\n"

        output_text += "【第四部分：末尾固定人设标签】\n"
        output_text += content["ending"] + "\n\n"

        output_text += "【第五部分：首评钩子】\n"
        for i, comment in enumerate(content["first_comment_options"], 1):
            output_text += f"{i}. {comment}\n"
        output_text += "\n"

        # 添加新闻链接
        if "news_links" in content and content["news_links"]:
            output_text += "【第六部分：参考新闻链接】\n"
            for i, link in enumerate(content["news_links"], 1):
                output_text += f"{i}. {link['title']}\n"
                output_text += f"   {link['url']}\n"
            output_text += "\n"

    # 保存到文件
    file_path = get_output_path("xiaohongshu", f"小红书文案_{format_time_filename()}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(output_text)

    print(f"小红书文案已生成: {file_path}")

    # 通过 webhook 发送小红书文案
    if CONFIG["ENABLE_NOTIFICATION"] and CONFIG["FEISHU_WEBHOOK_URL"]:
        try:
            # 构建发送内容（简化版，只发送标题和正文预览）
            send_text = "📕 **小红书文案已生成**\n\n"
            if ai_copywriting and isinstance(ai_copywriting, dict) and "copies" in ai_copywriting:
                for idx, copy in enumerate(ai_copywriting["copies"]):
                    content = copy.get("content", {})
                    send_text += f"--- 第 {idx+1} 条 ---\n"
                    send_text += f"📰 **{copy.get('news_title', '')}**\n"
                    send_text += f"📢 来源: {copy.get('news_source', '')}\n\n"
                    send_text += f"【标题参考】\n"
                    for title in content.get("title_options", [])[:2]:
                        send_text += f"• {title}\n"
                    send_text += "\n"
                    send_text += f"【正文预览】\n{content.get('body', '')[:300]}...\n\n"
            else:
                send_text += f"📝 完整文案请查看文件: {file_path}\n"
            
            # 调用飞书 webhook 发送
            import requests
            headers = {"Content-Type": "application/json"}
            payload = {
                "msg_type": "text",
                "content": {
                    "text": send_text
                }
            }
            response = requests.post(CONFIG["FEISHU_WEBHOOK_URL"], json=payload, headers=headers)
            if response.status_code == 200:
                print("小红书文案已通过 webhook 发送成功")
            else:
                print(f"webhook 发送失败: {response.status_code}")
        except Exception as e:
            print(f"发送小红书文案到 webhook 时发生错误: {e}")

    return file_path


# === 报告生成 ===
def prepare_report_data(
        stats: List[Dict],
        failed_ids: Optional[List] = None,
        new_titles: Optional[Dict] = None,
        id_to_name: Optional[Dict] = None,
        mode: str = "daily",
) -> Dict:
    """准备报告数据"""
    processed_new_titles = []

    # 在增量模式下隐藏新增新闻区域
    hide_new_section = mode == "incremental"

    # 只有在非隐藏模式下才处理新增新闻部分
    if not hide_new_section:
        filtered_new_titles = {}
        if new_titles and id_to_name:
            word_groups, filter_words = load_frequency_words()
            for source_id, titles_data in new_titles.items():
                filtered_titles = {}
                for title, title_data in titles_data.items():
                    if matches_word_groups(title, word_groups, filter_words):
                        filtered_titles[title] = title_data
                if filtered_titles:
                    filtered_new_titles[source_id] = filtered_titles

        if filtered_new_titles and id_to_name:
            for source_id, titles_data in filtered_new_titles.items():
                source_name = id_to_name.get(source_id, source_id)
                source_titles = []

                for title, title_data in titles_data.items():
                    url = title_data.get("url", "")
                    mobile_url = title_data.get("mobileUrl", "")
                    ranks = title_data.get("ranks", [])

                    processed_title = {
                        "title": title,
                        "source_name": source_name,
                        "time_display": "",
                        "count": 1,
                        "ranks": ranks,
                        "rank_threshold": CONFIG["RANK_THRESHOLD"],
                        "url": url,
                        "mobile_url": mobile_url,
                        "is_new": True,
                    }
                    source_titles.append(processed_title)

                if source_titles:
                    processed_new_titles.append(
                        {
                            "source_id": source_id,
                            "source_name": source_name,
                            "titles": source_titles,
                        }
                    )

    processed_stats = []
    ai_copywriting = None
    
    for stat in stats:
        # 检查是否有AI生成的文案
        if "ai_copywriting" in stat:
            ai_copywriting = stat["ai_copywriting"]
            # 如果是专门的AI文案条目且没有count，跳过
            if stat.get("count", 0) <= 0 and not stat.get("titles"):
                continue
        
        if stat["count"] <= 0 and not (stat.get("word") == "AI文案"):
            continue

        processed_titles = []
        for title_data in stat["titles"]:
            processed_title = {
                "title": title_data["title"],
                "source_name": title_data["source_name"],
                "time_display": title_data["time_display"],
                "count": title_data["count"],
                "ranks": title_data["ranks"],
                "rank_threshold": title_data["rank_threshold"],
                "url": title_data.get("url", ""),
                "mobile_url": title_data.get("mobileUrl", ""),
                "is_new": title_data.get("is_new", False),
                "ai_score": title_data.get("ai_score"),
                "ai_reason": title_data.get("ai_reason"),
            }
            processed_titles.append(processed_title)

        processed_stat = {
            "word": stat["word"],
            "count": stat["count"],
            "percentage": stat.get("percentage", 0),
            "titles": processed_titles,
        }
        
        # 如果这个stat有AI文案，也添加进去
        if "ai_copywriting" in stat:
            processed_stat["ai_copywriting"] = stat["ai_copywriting"]
            
        processed_stats.append(processed_stat)

    return {
        "stats": processed_stats,
        "new_titles": processed_new_titles,
        "failed_ids": failed_ids or [],
        "total_new_count": sum(
            len(source["titles"]) for source in processed_new_titles
        ),
        "ai_copywriting": ai_copywriting,
    }


def format_title_for_platform(
        platform: str, title_data: Dict, show_source: bool = True
) -> str:
    """统一的标题格式化方法"""
    rank_display = format_rank_display(
        title_data["ranks"], title_data["rank_threshold"], platform
    )

    link_url = title_data["mobile_url"] or title_data["url"]

    cleaned_title = clean_title(title_data["title"])

    if platform == "feishu":
        if link_url:
            formatted_title = f"[{cleaned_title}]({link_url})"
        else:
            formatted_title = cleaned_title

        title_prefix = "🆕 " if title_data.get("is_new") else ""

        if show_source:
            result = f"<font color='grey'>[{title_data['source_name']}]</font> {title_prefix}{formatted_title}"
        else:
            result = f"{title_prefix}{formatted_title}"

        if rank_display:
            result += f" {rank_display}"
        if title_data["time_display"]:
            result += f" <font color='grey'>- {title_data['time_display']}</font>"
        if title_data["count"] > 1:
            result += f" <font color='green'>({title_data['count']}次)</font>"

        return result

    elif platform == "dingtalk":
        if link_url:
            formatted_title = f"[{cleaned_title}]({link_url})"
        else:
            formatted_title = cleaned_title

        title_prefix = "🆕 " if title_data.get("is_new") else ""

        if show_source:
            result = f"[{title_data['source_name']}] {title_prefix}{formatted_title}"
        else:
            result = f"{title_prefix}{formatted_title}"

        if rank_display:
            result += f" {rank_display}"
        if title_data["time_display"]:
            result += f" - {title_data['time_display']}"
        if title_data["count"] > 1:
            result += f" ({title_data['count']}次)"

        return result

    elif platform == "wework":
        if link_url:
            formatted_title = f"[{cleaned_title}]({link_url})"
        else:
            formatted_title = cleaned_title

        title_prefix = "🆕 " if title_data.get("is_new") else ""

        if show_source:
            result = f"[{title_data['source_name']}] {title_prefix}{formatted_title}"
        else:
            result = f"{title_prefix}{formatted_title}"

        if rank_display:
            result += f" {rank_display}"
        if title_data["time_display"]:
            result += f" - {title_data['time_display']}"
        if title_data["count"] > 1:
            result += f" ({title_data['count']}次)"

        return result

    elif platform == "telegram":
        if link_url:
            formatted_title = f'<a href="{link_url}">{html_escape(cleaned_title)}</a>'
        else:
            formatted_title = cleaned_title

        title_prefix = "🆕 " if title_data.get("is_new") else ""

        if show_source:
            result = f"[{title_data['source_name']}] {title_prefix}{formatted_title}"
        else:
            result = f"{title_prefix}{formatted_title}"

        if rank_display:
            result += f" {rank_display}"
        if title_data["time_display"]:
            result += f" <code>- {title_data['time_display']}</code>"
        if title_data["count"] > 1:
            result += f" <code>({title_data['count']}次)</code>"

        return result

    elif platform == "html":
        rank_display = format_rank_display(
            title_data["ranks"], title_data["rank_threshold"], "html"
        )

        link_url = title_data["mobile_url"] or title_data["url"]

        escaped_title = html_escape(cleaned_title)
        escaped_source_name = html_escape(title_data["source_name"])

        if link_url:
            escaped_url = html_escape(link_url)
            formatted_title = f'[{escaped_source_name}] <a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
        else:
            formatted_title = (
                f'[{escaped_source_name}] <span class="no-link">{escaped_title}</span>'
            )

        if rank_display:
            formatted_title += f" {rank_display}"
        if title_data["time_display"]:
            escaped_time = html_escape(title_data["time_display"])
            formatted_title += f" <font color='grey'>- {escaped_time}</font>"
        if title_data["count"] > 1:
            formatted_title += f" <font color='green'>({title_data['count']}次)</font>"

        if title_data.get("is_new"):
            formatted_title = f"<div class='new-title'>🆕 {formatted_title}</div>"

        return formatted_title

    else:
        return cleaned_title


def generate_html_report(
        stats: List[Dict],
        total_titles: int,
        failed_ids: Optional[List] = None,
        new_titles: Optional[Dict] = None,
        id_to_name: Optional[Dict] = None,
        mode: str = "daily",
        is_daily_summary: bool = False,
) -> str:
    """生成HTML报告"""
    if is_daily_summary:
        if mode == "current":
            filename = "当前榜单汇总.html"
        elif mode == "incremental":
            filename = "当日增量.html"
        else:
            filename = "当日汇总.html"
    else:
        filename = f"{format_time_filename()}.html"

    file_path = get_output_path("html", filename)

    report_data = prepare_report_data(stats, failed_ids, new_titles, id_to_name, mode)

    html_content = render_html_content(
        report_data, total_titles, is_daily_summary, mode
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    if is_daily_summary:
        root_file_path = Path("index.html")
        with open(root_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    return file_path


def render_html_content(
        report_data: Dict,
        total_titles: int,
        is_daily_summary: bool = False,
        mode: str = "daily",
) -> str:
    """渲染HTML内容"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>热点新闻分析</title>
        <style>
            * { box-sizing: border-box; }
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
                margin: 0; 
                padding: 16px; 
                background: #fafafa;
                color: #333;
                line-height: 1.5;
            }

            .container {
                max-width: 600px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 16px rgba(0,0,0,0.06);
            }

            .header {
                background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
                color: white;
                padding: 32px 24px;
                text-align: center;
            }

            .header-title {
                font-size: 22px;
                font-weight: 700;
                margin: 0 0 20px 0;
            }

            .header-info {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                font-size: 14px;
                opacity: 0.95;
            }

            .info-item {
                text-align: center;
            }

            .info-label {
                display: block;
                font-size: 12px;
                opacity: 0.8;
                margin-bottom: 4px;
            }

            .info-value {
                font-weight: 600;
                font-size: 16px;
            }

            .content {
                padding: 24px;
            }

            .word-group {
                margin-bottom: 40px;
            }

            .word-group:first-child {
                margin-top: 0;
            }

            .word-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 20px;
                padding-bottom: 8px;
                border-bottom: 1px solid #f0f0f0;
            }

            .word-info {
                display: flex;
                align-items: center;
                gap: 12px;
            }

            .word-name {
                font-size: 17px;
                font-weight: 600;
                color: #1a1a1a;
            }

            .word-count {
                color: #666;
                font-size: 13px;
                font-weight: 500;
            }

            .word-count.hot { color: #dc2626; font-weight: 600; }
            .word-count.warm { color: #ea580c; font-weight: 600; }

            .word-index {
                color: #999;
                font-size: 12px;
            }

            .news-item {
                margin-bottom: 20px;
                padding: 16px 0;
                border-bottom: 1px solid #f5f5f5;
                position: relative;
                display: flex;
                gap: 12px;
                align-items: center;
            }

            .news-item:last-child {
                border-bottom: none;
            }

            .news-item.new::after {
                content: "NEW";
                position: absolute;
                top: 12px;
                right: 0;
                background: #fbbf24;
                color: #92400e;
                font-size: 9px;
                font-weight: 700;
                padding: 3px 6px;
                border-radius: 4px;
                letter-spacing: 0.5px;
            }

            .news-number {
                color: #999;
                font-size: 13px;
                font-weight: 600;
                min-width: 20px;
                text-align: center;
                flex-shrink: 0;
                background: #f8f9fa;
                border-radius: 50%;
                width: 24px;
                height: 24px;
                display: flex;
                align-items: center;
                justify-content: center;
                align-self: flex-start;
                margin-top: 8px;
            }

            .news-content {
                flex: 1;
                min-width: 0;
                padding-right: 40px;
            }

            .news-item.new .news-content {
                padding-right: 50px;
            }

            .news-header {
                display: flex;
                align-items: center;
                gap: 8px;
                margin-bottom: 8px;
                flex-wrap: wrap;
            }

            .source-name {
                color: #666;
                font-size: 12px;
                font-weight: 500;
            }

            .rank-num {
                color: #fff;
                background: #6b7280;
                font-size: 10px;
                font-weight: 700;
                padding: 2px 6px;
                border-radius: 10px;
                min-width: 18px;
                text-align: center;
            }

            .rank-num.top { background: #dc2626; }
            .rank-num.high { background: #ea580c; }

            .time-info {
                color: #999;
                font-size: 11px;
            }

            .count-info {
                color: #059669;
                font-size: 11px;
                font-weight: 500;
            }

            .news-title {
                font-size: 15px;
                line-height: 1.4;
                color: #1a1a1a;
                margin: 0;
            }

            .news-link {
                color: #2563eb;
                text-decoration: none;
            }

            .news-link:hover {
                text-decoration: underline;
            }

            .news-link:visited {
                color: #7c3aed;
            }

            .new-section {
                margin-top: 40px;
                padding-top: 24px;
                border-top: 2px solid #f0f0f0;
            }

            .new-section-title {
                color: #1a1a1a;
                font-size: 16px;
                font-weight: 600;
                margin: 0 0 20px 0;
            }

            .new-source-group {
                margin-bottom: 24px;
            }

            .new-source-title {
                color: #666;
                font-size: 13px;
                font-weight: 500;
                margin: 0 0 12px 0;
                padding-bottom: 6px;
                border-bottom: 1px solid #f5f5f5;
            }

            .new-item {
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 8px 0;
                border-bottom: 1px solid #f9f9f9;
            }

            .new-item:last-child {
                border-bottom: none;
            }

            .new-item-number {
                color: #999;
                font-size: 12px;
                font-weight: 600;
                min-width: 18px;
                text-align: center;
                flex-shrink: 0;
                background: #f8f9fa;
                border-radius: 50%;
                width: 20px;
                height: 20px;
                display: flex;
                align-items: center;
                justify-content: center;
            }

            .new-item-rank {
                color: #fff;
                background: #6b7280;
                font-size: 10px;
                font-weight: 700;
                padding: 3px 6px;
                border-radius: 8px;
                min-width: 20px;
                text-align: center;
                flex-shrink: 0;
            }

            .new-item-rank.top { background: #dc2626; }
            .new-item-rank.high { background: #ea580c; }

            .new-item-content {
                flex: 1;
                min-width: 0;
            }

            .new-item-title {
                font-size: 14px;
                line-height: 1.4;
                color: #1a1a1a;
                margin: 0;
            }

            .error-section {
                background: #fef2f2;
                border: 1px solid #fecaca;
                border-radius: 8px;
                padding: 16px;
                margin-bottom: 24px;
            }

            .error-title {
                color: #dc2626;
                font-size: 14px;
                font-weight: 600;
                margin: 0 0 8px 0;
            }

            .error-list {
                list-style: none;
                padding: 0;
                margin: 0;
            }

            .error-item {
                color: #991b1b;
                font-size: 13px;
                padding: 2px 0;
                font-family: 'SF Mono', Consolas, monospace;
            }

            @media (max-width: 480px) {
                body { padding: 12px; }
                .header { padding: 24px 20px; }
                .content { padding: 20px; }
                .header-info { grid-template-columns: 1fr; gap: 12px; }
                .news-header { gap: 6px; }
                .news-content { padding-right: 45px; }
                .news-item { gap: 8px; }
                .new-item { gap: 8px; }
                .news-number { width: 20px; height: 20px; font-size: 12px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div class="header-title">热点新闻分析</div>
                <div class="header-info">
                    <div class="info-item">
                        <span class="info-label">报告类型</span>
                        <span class="info-value">"""

    # 处理报告类型显示
    if is_daily_summary:
        if mode == "current":
            html += "当前榜单"
        elif mode == "incremental":
            html += "增量模式"
        else:
            html += "当日汇总"
    else:
        html += "实时分析"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">新闻总数</span>
                        <span class="info-value">"""

    html += f"{total_titles} 条"

    # 计算筛选后的热点新闻数量
    hot_news_count = sum(len(stat["titles"]) for stat in report_data["stats"])

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">热点新闻</span>
                        <span class="info-value">"""

    html += f"{hot_news_count} 条"

    html += """</span>
                    </div>
                    <div class="info-item">
                        <span class="info-label">生成时间</span>
                        <span class="info-value">"""

    now = get_beijing_time()
    html += now.strftime("%m-%d %H:%M")

    html += """</span>
                    </div>
                </div>
        </div>

        <div class="content">"""

    # 处理失败ID错误信息
    if report_data["failed_ids"]:
        html += """
                <div class="error-section">
                    <div class="error-title">⚠️ 请求失败的平台</div>
                    <ul class="error-list">"""
        for id_value in report_data["failed_ids"]:
            html += f'<li class="error-item">{html_escape(id_value)}</li>'
        html += """
                    </ul>
                </div>"""

    # 处理主要统计数据
    if report_data["stats"]:
        total_count = len(report_data["stats"])

        for i, stat in enumerate(report_data["stats"], 1):
            count = stat["count"]

            # 确定热度等级
            if count >= 10:
                count_class = "hot"
            elif count >= 5:
                count_class = "warm"
            else:
                count_class = ""

            escaped_word = html_escape(stat["word"])

            html += f"""
                <div class="word-group">
                    <div class="word-header">
                        <div class="word-info">
                            <div class="word-name">{escaped_word}</div>
                            <div class="word-count {count_class}">{count} 条</div>
                        </div>
                        <div class="word-index">{i}/{total_count}</div>
                    </div>"""

            # 处理每个词组下的新闻标题，给每条新闻标上序号
            for j, title_data in enumerate(stat["titles"], 1):
                is_new = title_data.get("is_new", False)
                new_class = "new" if is_new else ""

                html += f"""
                    <div class="news-item {new_class}">
                        <div class="news-number">{j}</div>
                        <div class="news-content">
                            <div class="news-header">
                                <span class="source-name">{html_escape(title_data["source_name"])}</span>"""

                # 处理排名显示
                ranks = title_data.get("ranks", [])
                if ranks:
                    min_rank = min(ranks)
                    max_rank = max(ranks)
                    rank_threshold = title_data.get("rank_threshold", 10)

                    # 确定排名等级
                    if min_rank <= 3:
                        rank_class = "top"
                    elif min_rank <= rank_threshold:
                        rank_class = "high"
                    else:
                        rank_class = ""

                    if min_rank == max_rank:
                        rank_text = str(min_rank)
                    else:
                        rank_text = f"{min_rank}-{max_rank}"

                    html += f'<span class="rank-num {rank_class}">{rank_text}</span>'

                # 处理时间显示
                time_display = title_data.get("time_display", "")
                if time_display:
                    # 简化时间显示格式，将波浪线替换为~
                    simplified_time = (
                        time_display.replace(" ~ ", "~")
                        .replace("[", "")
                        .replace("]", "")
                    )
                    html += (
                        f'<span class="time-info">{html_escape(simplified_time)}</span>'
                    )

                # 处理出现次数
                count_info = title_data.get("count", 1)
                if count_info > 1:
                    html += f'<span class="count-info">{count_info}次</span>'

                html += """
                            </div>
                            <div class="news-title">"""

                # 处理标题和链接
                escaped_title = html_escape(title_data["title"])
                link_url = title_data.get("mobile_url") or title_data.get("url", "")

                if link_url:
                    escaped_url = html_escape(link_url)
                    html += f'<a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
                else:
                    html += escaped_title

                html += """
                            </div>
                        </div>
                    </div>"""

            html += """
                </div>"""

    # 处理新增新闻区域
    if report_data["new_titles"]:
        html += f"""
                <div class="new-section">
                    <div class="new-section-title">本次新增热点 (共 {report_data['total_new_count']} 条)</div>"""

        for source_data in report_data["new_titles"]:
            escaped_source = html_escape(source_data["source_name"])
            titles_count = len(source_data["titles"])

            html += f"""
                    <div class="new-source-group">
                        <div class="new-source-title">{escaped_source} · {titles_count}条</div>"""

            # 为新增新闻也添加序号
            for idx, title_data in enumerate(source_data["titles"], 1):
                ranks = title_data.get("ranks", [])

                # 处理新增新闻的排名显示
                rank_class = ""
                if ranks:
                    min_rank = min(ranks)
                    if min_rank <= 3:
                        rank_class = "top"
                    elif min_rank <= title_data.get("rank_threshold", 10):
                        rank_class = "high"

                    if len(ranks) == 1:
                        rank_text = str(ranks[0])
                    else:
                        rank_text = f"{min(ranks)}-{max(ranks)}"
                else:
                    rank_text = "?"

                html += f"""
                        <div class="new-item">
                            <div class="new-item-number">{idx}</div>
                            <div class="new-item-rank {rank_class}">{rank_text}</div>
                            <div class="new-item-content">
                                <div class="new-item-title">"""

                # 处理新增新闻的链接
                escaped_title = html_escape(title_data["title"])
                link_url = title_data.get("mobile_url") or title_data.get("url", "")

                if link_url:
                    escaped_url = html_escape(link_url)
                    html += f'<a href="{escaped_url}" target="_blank" class="news-link">{escaped_title}</a>'
                else:
                    html += escaped_title

                html += """
                                </div>
                            </div>
                        </div>"""

            html += """
                    </div>"""

        html += """
                </div>"""

    html += """
            </div>
        </div>
    </body>
    </html>
    """

    return html


def render_feishu_content(
        report_data: Dict, update_info: Optional[Dict] = None, mode: str = "daily"
) -> str:
    """渲染飞书内容"""
    text_content = ""

    if report_data["stats"]:
        text_content += f"📊 **热点词汇统计**\n\n"

    total_count = len(report_data["stats"])

    for i, stat in enumerate(report_data["stats"]):
        word = stat["word"]
        count = stat["count"]

        sequence_display = f"<font color='grey'>[{i + 1}/{total_count}]</font>"

        if count >= 10:
            text_content += f"🔥 {sequence_display} **{word}** : <font color='red'>{count}</font> 条\n\n"
        elif count >= 5:
            text_content += f"📈 {sequence_display} **{word}** : <font color='orange'>{count}</font> 条\n\n"
        else:
            text_content += f"📌 {sequence_display} **{word}** : {count} 条\n\n"

        for j, title_data in enumerate(stat["titles"], 1):
            formatted_title = format_title_for_platform(
                "feishu", title_data, show_source=True
            )
            text_content += f"  {j}. {formatted_title}\n"

            if j < len(stat["titles"]):
                text_content += "\n"

        if i < len(report_data["stats"]) - 1:
            text_content += f"\n{CONFIG['FEISHU_MESSAGE_SEPARATOR']}\n\n"

    if not text_content:
        if mode == "incremental":
            mode_text = "增量模式下暂无新增匹配的热点词汇"
        elif mode == "current":
            mode_text = "当前榜单模式下暂无匹配的热点词汇"
        else:
            mode_text = "暂无匹配的热点词汇"
        text_content = f"📭 {mode_text}\n\n"

    if report_data["new_titles"]:
        if text_content and "暂无匹配" not in text_content:
            text_content += f"\n{CONFIG['FEISHU_MESSAGE_SEPARATOR']}\n\n"

        text_content += (
            f"🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        )

        for source_data in report_data["new_titles"]:
            text_content += (
                f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n"
            )

            for j, title_data in enumerate(source_data["titles"], 1):
                title_data_copy = title_data.copy()
                title_data_copy["is_new"] = False
                formatted_title = format_title_for_platform(
                    "feishu", title_data_copy, show_source=False
                )
                text_content += f"  {j}. {formatted_title}\n"

            text_content += "\n"

    if report_data["failed_ids"]:
        if text_content and "暂无匹配" not in text_content:
            text_content += f"\n{CONFIG['FEISHU_MESSAGE_SEPARATOR']}\n\n"

        text_content += "⚠️ **数据获取失败的平台：**\n\n"
        for i, id_value in enumerate(report_data["failed_ids"], 1):
            text_content += f"  • <font color='red'>{id_value}</font>\n"

    now = get_beijing_time()
    text_content += (
        f"\n\n<font color='grey'>更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}</font>"
    )

    if update_info:
        text_content += f"\n<font color='grey'>TrendRadar 发现新版本 {update_info['remote_version']}，当前 {update_info['current_version']}</font>"

    return text_content


def render_dingtalk_content(
        report_data: Dict, update_info: Optional[Dict] = None, mode: str = "daily"
) -> str:
    """渲染钉钉内容"""
    text_content = ""

    total_titles = sum(
        len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
    )
    now = get_beijing_time()

    text_content += f"**总新闻数：** {total_titles}\n\n"
    text_content += f"**时间：** {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    text_content += f"**类型：** 热点分析报告\n\n"

    text_content += "---\n\n"

    if report_data["stats"]:
        text_content += f"📊 **热点词汇统计**\n\n"

        total_count = len(report_data["stats"])

        for i, stat in enumerate(report_data["stats"]):
            word = stat["word"]
            count = stat["count"]

            sequence_display = f"[{i + 1}/{total_count}]"

            if count >= 10:
                text_content += f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
            elif count >= 5:
                text_content += f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
            else:
                text_content += f"📌 {sequence_display} **{word}** : {count} 条\n\n"

            for j, title_data in enumerate(stat["titles"], 1):
                formatted_title = format_title_for_platform(
                    "dingtalk", title_data, show_source=True
                )
                text_content += f"  {j}. {formatted_title}\n"

                if j < len(stat["titles"]):
                    text_content += "\n"

            if i < len(report_data["stats"]) - 1:
                text_content += f"\n---\n\n"

    if not report_data["stats"]:
        if mode == "incremental":
            mode_text = "增量模式下暂无新增匹配的热点词汇"
        elif mode == "current":
            mode_text = "当前榜单模式下暂无匹配的热点词汇"
        else:
            mode_text = "暂无匹配的热点词汇"
        text_content += f"📭 {mode_text}\n\n"

    if report_data["new_titles"]:
        if text_content and "暂无匹配" not in text_content:
            text_content += f"\n---\n\n"

        text_content += (
            f"🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        )

        for source_data in report_data["new_titles"]:
            text_content += f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"

            for j, title_data in enumerate(source_data["titles"], 1):
                title_data_copy = title_data.copy()
                title_data_copy["is_new"] = False
                formatted_title = format_title_for_platform(
                    "dingtalk", title_data_copy, show_source=False
                )
                text_content += f"  {j}. {formatted_title}\n"

            text_content += "\n"

    if report_data["failed_ids"]:
        if text_content and "暂无匹配" not in text_content:
            text_content += f"\n---\n\n"

        text_content += "⚠️ **数据获取失败的平台：**\n\n"
        for i, id_value in enumerate(report_data["failed_ids"], 1):
            text_content += f"  • **{id_value}**\n"

    text_content += f"\n\n> 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"

    if update_info:
        text_content += f"\n> TrendRadar 发现新版本 **{update_info['remote_version']}**，当前 **{update_info['current_version']}**"

    return text_content


def split_content_into_batches(
        report_data: Dict,
        format_type: str,
        update_info: Optional[Dict] = None,
        max_bytes: int = CONFIG["MESSAGE_BATCH_SIZE"],
        mode: str = "daily",
) -> List[str]:
    """分批处理消息内容，确保词组标题+至少第一条新闻的完整性"""
    batches = []

    total_titles = sum(
        len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
    )
    now = get_beijing_time()

    base_header = ""
    if format_type == "wework":
        base_header = f"**总新闻数：** {total_titles}\n\n\n\n"
    elif format_type == "telegram":
        base_header = f"总新闻数： {total_titles}\n\n"

    base_footer = ""
    if format_type == "wework":
        base_footer = f"\n\n\n> 更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\n> TrendRadar 发现新版本 **{update_info['remote_version']}**，当前 **{update_info['current_version']}**"
    elif format_type == "telegram":
        base_footer = f"\n\n更新时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
        if update_info:
            base_footer += f"\nTrendRadar 发现新版本 {update_info['remote_version']}，当前 {update_info['current_version']}"

    stats_header = ""
    if report_data["stats"]:
        if format_type == "wework":
            stats_header = f"📊 **热点词汇统计**\n\n"
        elif format_type == "telegram":
            stats_header = f"📊 热点词汇统计\n\n"
    
    if (
            not report_data["stats"]
            and not report_data["new_titles"]
            and not report_data["failed_ids"]
    ):
        if mode == "incremental":
            mode_text = "增量模式下暂无新增匹配的热点词汇"
        elif mode == "current":
            mode_text = "当前榜单模式下暂无匹配的热点词汇"
        else:
            mode_text = "暂无匹配的热点词汇"
        simple_content = f"📭 {mode_text}\n\n"
        final_content = base_header + simple_content + base_footer
        batches.append(final_content)
        return batches

    # 处理热点词汇统计
    if report_data["stats"]:
        total_count = len(report_data["stats"])
        
        # 初始化当前批次
        current_batch = base_header
        current_batch_has_content = False

        # 添加统计标题
        test_content = current_batch + stats_header
        if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                < max_bytes
        ):
            current_batch = test_content
            current_batch_has_content = True
        else:
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + stats_header
            current_batch_has_content = True

        # 逐个处理词组（确保词组标题+第一条新闻的原子性）
        for i, stat in enumerate(report_data["stats"]):
            word = stat["word"]
            count = stat["count"]
            sequence_display = f"[{i + 1}/{total_count}]"

            # 构建词组标题
            word_header = ""
            if format_type == "wework":
                if count >= 10:
                    word_header = (
                        f"🔥 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                elif count >= 5:
                    word_header = (
                        f"📈 {sequence_display} **{word}** : **{count}** 条\n\n"
                    )
                else:
                    word_header = f"📌 {sequence_display} **{word}** : {count} 条\n\n"
            elif format_type == "telegram":
                if count >= 10:
                    word_header = f"🔥 {sequence_display} {word} : {count} 条\n\n"
                elif count >= 5:
                    word_header = f"📈 {sequence_display} {word} : {count} 条\n\n"
                else:
                    word_header = f"📌 {sequence_display} {word} : {count} 条\n\n"

            # 构建第一条新闻
            first_news_line = ""
            if stat["titles"]:
                first_title_data = stat["titles"][0]
                if format_type == "wework":
                    formatted_title = format_title_for_platform(
                        "wework", first_title_data, show_source=True
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", first_title_data, show_source=True
                    )
                else:
                    formatted_title = f"{first_title_data['title']}"

                first_news_line = f"  1. {formatted_title}\n"
                if len(stat["titles"]) > 1:
                    first_news_line += "\n"

            # 原子性检查：词组标题+第一条新闻必须一起处理
            word_with_first_news = word_header + first_news_line
            test_content = current_batch + word_with_first_news

            if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    >= max_bytes
            ):
                # 当前批次容纳不下，开启新批次
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + stats_header + word_with_first_news
                current_batch_has_content = True
                start_index = 1
            else:
                current_batch = test_content
                current_batch_has_content = True
                start_index = 1

            # 处理剩余新闻条目
            for j in range(start_index, len(stat["titles"])):
                title_data = stat["titles"][j]
                if format_type == "wework":
                    formatted_title = format_title_for_platform(
                        "wework", title_data, show_source=True
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data, show_source=True
                    )
                else:
                    formatted_title = f"{title_data['title']}"

                news_line = f"  {j + 1}. {formatted_title}\n"
                if j < len(stat["titles"]) - 1:
                    news_line += "\n"

                test_content = current_batch + news_line
                if (
                        len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                        >= max_bytes
                ):
                    if current_batch_has_content:
                        batches.append(current_batch + base_footer)
                    current_batch = base_header + stats_header + word_header + news_line
                    current_batch_has_content = True
                else:
                    current_batch = test_content
                    current_batch_has_content = True

            # 词组间分隔符
            if i < len(report_data["stats"]) - 1:
                separator = ""
                if format_type == "wework":
                    separator = f"\n\n\n\n"
                elif format_type == "telegram":
                    separator = f"\n\n"

                test_content = current_batch + separator
                if (
                        len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                        < max_bytes
                ):
                    current_batch = test_content

    # 处理新增新闻（同样确保来源标题+第一条新闻的原子性）
    if report_data["new_titles"]:
        new_header = ""
        if format_type == "wework":
            new_header = f"\n\n\n\n🆕 **本次新增热点新闻** (共 {report_data['total_new_count']} 条)\n\n"
        elif format_type == "telegram":
            new_header = (
                f"\n\n🆕 本次新增热点新闻 (共 {report_data['total_new_count']} 条)\n\n"
            )

        test_content = current_batch + new_header
        if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                >= max_bytes
        ):
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + new_header
            current_batch_has_content = True
        else:
            current_batch = test_content
            current_batch_has_content = True

        # 逐个处理新增新闻来源
        for source_data in report_data["new_titles"]:
            source_header = ""
            if format_type == "wework":
                source_header = f"**{source_data['source_name']}** ({len(source_data['titles'])} 条):\n\n"
            elif format_type == "telegram":
                source_header = f"{source_data['source_name']} ({len(source_data['titles'])} 条):\n\n"

            # 构建第一条新增新闻
            first_news_line = ""
            if source_data["titles"]:
                first_title_data = source_data["titles"][0]
                title_data_copy = first_title_data.copy()
                title_data_copy["is_new"] = False

                if format_type == "wework":
                    formatted_title = format_title_for_platform(
                        "wework", title_data_copy, show_source=False
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data_copy, show_source=False
                    )
                else:
                    formatted_title = f"{title_data_copy['title']}"

                first_news_line = f"  1. {formatted_title}\n"

            # 原子性检查：来源标题+第一条新闻
            source_with_first_news = source_header + first_news_line
            test_content = current_batch + source_with_first_news

            if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    >= max_bytes
            ):
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + new_header + source_with_first_news
                current_batch_has_content = True
                start_index = 1
            else:
                current_batch = test_content
                current_batch_has_content = True
                start_index = 1

            # 处理剩余新增新闻
            for j in range(start_index, len(source_data["titles"])):
                title_data = source_data["titles"][j]
                title_data_copy = title_data.copy()
                title_data_copy["is_new"] = False

                if format_type == "wework":
                    formatted_title = format_title_for_platform(
                        "wework", title_data_copy, show_source=False
                    )
                elif format_type == "telegram":
                    formatted_title = format_title_for_platform(
                        "telegram", title_data_copy, show_source=False
                    )
                else:
                    formatted_title = f"{title_data_copy['title']}"

                news_line = f"  {j + 1}. {formatted_title}\n"

                test_content = current_batch + news_line
                if (
                        len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                        >= max_bytes
                ):
                    if current_batch_has_content:
                        batches.append(current_batch + base_footer)
                    current_batch = base_header + new_header + source_header + news_line
                    current_batch_has_content = True
                else:
                    current_batch = test_content
                    current_batch_has_content = True

            current_batch += "\n"

    if report_data["failed_ids"]:
        failed_header = ""
        if format_type == "wework":
            failed_header = f"\n\n\n\n⚠️ **数据获取失败的平台：**\n\n"
        elif format_type == "telegram":
            failed_header = f"\n\n⚠️ 数据获取失败的平台：\n\n"

        test_content = current_batch + failed_header
        if (
                len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                >= max_bytes
        ):
            if current_batch_has_content:
                batches.append(current_batch + base_footer)
            current_batch = base_header + failed_header
            current_batch_has_content = True
        else:
            current_batch = test_content
            current_batch_has_content = True

        for i, id_value in enumerate(report_data["failed_ids"], 1):
            failed_line = f"  • {id_value}\n"
            test_content = current_batch + failed_line
            if (
                    len(test_content.encode("utf-8")) + len(base_footer.encode("utf-8"))
                    >= max_bytes
            ):
                if current_batch_has_content:
                    batches.append(current_batch + base_footer)
                current_batch = base_header + failed_header + failed_line
                current_batch_has_content = True
            else:
                current_batch = test_content
                current_batch_has_content = True

    # 完成最后批次
    if current_batch_has_content:
        batches.append(current_batch + base_footer)

    return batches


def send_to_webhooks(
        stats: List[Dict],
        failed_ids: Optional[List] = None,
        report_type: str = "当日汇总",
        new_titles: Optional[Dict] = None,
        id_to_name: Optional[Dict] = None,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
) -> Dict[str, bool]:
    """发送数据到多个webhook平台（优先发送小红书文案）"""
    results = {}

    if CONFIG["SILENT_PUSH"]["ENABLED"]:
        push_manager = PushRecordManager()
        time_range_start = CONFIG["SILENT_PUSH"]["TIME_RANGE"]["START"]
        time_range_end = CONFIG["SILENT_PUSH"]["TIME_RANGE"]["END"]

        if not push_manager.is_in_time_range(time_range_start, time_range_end):
            now = get_beijing_time()
            print(
                f"静默模式：当前时间 {now.strftime('%H:%M')} 不在推送时间范围 {time_range_start}-{time_range_end} 内，跳过推送")
            return results

        if CONFIG["SILENT_PUSH"]["ONCE_PER_DAY"]:
            if push_manager.has_pushed_today():
                print(f"静默模式：今天已推送过，跳过本次推送")
                return results
            else:
                print(f"静默模式：今天首次推送")

    # 检查是否有 AI 生成的小红书文案
    ai_copywriting = None
    if stats and stats[0].get("ai_copywriting"):
        ai_copywriting = stats[0]["ai_copywriting"]
    
    # 只发送AI生成的小红书文案，不发送新闻汇总
    if ai_copywriting and isinstance(ai_copywriting, dict) and "copies" in ai_copywriting:
        copies = ai_copywriting["copies"]
        
        feishu_url = CONFIG["FEISHU_WEBHOOK_URL"]
        dingtalk_url = CONFIG["DINGTALK_WEBHOOK_URL"]
        wework_url = CONFIG["WEWORK_WEBHOOK_URL"]
        telegram_token = CONFIG["TELEGRAM_BOT_TOKEN"]
        telegram_chat_id = CONFIG["TELEGRAM_CHAT_ID"]
        
        for idx, copy in enumerate(copies):
            news_title = copy.get("news_title", "")
            news_source = copy.get("news_source", "")
            content = copy.get("content", {})
            
            cover_options = content.get("cover_options", [])
            title_options = content.get("title_options", [])
            body = content.get("body", "")
            ending = content.get("ending", "")
            
            message = f"""📝 小红书文案 #{idx+1}

【来源新闻】
{news_title}
来源: {news_source}

【封面选项】
{chr(10).join(f"- {opt}" for opt in cover_options)}

【标题选项】
{chr(10).join(f"- {opt}" for opt in title_options)}

【正文】
{body}

{ending}"""
            
            # 发送到飞书
            if feishu_url:
                results["feishu"] = send_text_to_feishu(feishu_url, message)
            
            # 发送到钉钉
            if dingtalk_url:
                results["dingtalk"] = send_text_to_dingtalk(dingtalk_url, message)
            
            # 发送到企业微信
            if wework_url:
                results["wework"] = send_text_to_wework(wework_url, message)
            
            # 发送到 Telegram
            if telegram_token and telegram_chat_id:
                results["telegram"] = send_text_to_telegram(telegram_token, telegram_chat_id, message)
    
    else:
        # 没有AI生成的小红书文案，不发送任何通知
        print("没有AI生成的小红书文案，跳过通知发送")

    if not results:
        print("未配置任何webhook URL，跳过通知发送")

    # 如果成功发送了任何通知，且启用了每天只推一次，则记录推送
    if CONFIG["SILENT_PUSH"]["ENABLED"] and CONFIG["SILENT_PUSH"]["ONCE_PER_DAY"] and any(results.values()):
        push_manager = PushRecordManager()
        push_manager.record_push(report_type)

    return results


def send_to_feishu(
        webhook_url: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
) -> bool:
    """发送到飞书"""
    headers = {"Content-Type": "application/json"}

    text_content = render_feishu_content(report_data, update_info, mode)
    total_titles = sum(
        len(stat["titles"]) for stat in report_data["stats"] if stat["count"] > 0
    )

    now = get_beijing_time()
    payload = {
        "msg_type": "text",
        "content": {
            "total_titles": total_titles,
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "report_type": report_type,
            "text": text_content,
        },
    }

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    try:
        response = requests.post(
            webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
        )
        if response.status_code == 200:
            print(f"飞书通知发送成功 [{report_type}]")
            return True
        else:
            print(f"飞书通知发送失败 [{report_type}]，状态码：{response.status_code}")
            return False
    except Exception as e:
        print(f"飞书通知发送出错 [{report_type}]：{e}")
        return False


def send_to_dingtalk(
        webhook_url: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
) -> bool:
    """发送到钉钉"""
    headers = {"Content-Type": "application/json"}

    text_content = render_dingtalk_content(report_data, update_info, mode)

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"TrendRadar 热点分析报告 - {report_type}",
            "text": text_content,
        },
    }

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    try:
        response = requests.post(
            webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            if result.get("errcode") == 0:
                print(f"钉钉通知发送成功 [{report_type}]")
                return True
            else:
                print(f"钉钉通知发送失败 [{report_type}]，错误：{result.get('errmsg')}")
                return False
        else:
            print(f"钉钉通知发送失败 [{report_type}]，状态码：{response.status_code}")
            return False
    except Exception as e:
        print(f"钉钉通知发送出错 [{report_type}]：{e}")
        return False


def send_to_wework(
        webhook_url: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
) -> bool:
    """发送到企业微信（支持分批发送）"""
    headers = {"Content-Type": "application/json"}
    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 获取分批内容
    batches = split_content_into_batches(report_data, "wework", update_info, mode=mode)

    print(f"企业微信消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送企业微信第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        # 添加批次标识
        if len(batches) > 1:
            batch_header = f"**[第 {i}/{len(batches)} 批次]**\n\n"
            batch_content = batch_header + batch_content

        payload = {"msgtype": "markdown", "markdown": {"content": batch_content}}

        try:
            response = requests.post(
                webhook_url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    print(f"企业微信第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"企业微信第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{result.get('errmsg')}"
                    )
                    return False
            else:
                print(
                    f"企业微信第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"企业微信第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"企业微信所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


def send_to_telegram(
        bot_token: str,
        chat_id: str,
        report_data: Dict,
        report_type: str,
        update_info: Optional[Dict] = None,
        proxy_url: Optional[str] = None,
        mode: str = "daily",
) -> bool:
    """发送到Telegram（支持分批发送）"""
    headers = {"Content-Type": "application/json"}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    proxies = None
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}

    # 获取分批内容
    batches = split_content_into_batches(
        report_data, "telegram", update_info, mode=mode
    )

    print(f"Telegram消息分为 {len(batches)} 批次发送 [{report_type}]")

    # 逐批发送
    for i, batch_content in enumerate(batches, 1):
        batch_size = len(batch_content.encode("utf-8"))
        print(
            f"发送Telegram第 {i}/{len(batches)} 批次，大小：{batch_size} 字节 [{report_type}]"
        )

        # 添加批次标识
        if len(batches) > 1:
            batch_header = f"<b>[第 {i}/{len(batches)} 批次]</b>\n\n"
            batch_content = batch_header + batch_content

        payload = {
            "chat_id": chat_id,
            "text": batch_content,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                url, headers=headers, json=payload, proxies=proxies, timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("ok"):
                    print(f"Telegram第 {i}/{len(batches)} 批次发送成功 [{report_type}]")
                    # 批次间间隔
                    if i < len(batches):
                        time.sleep(CONFIG["BATCH_SEND_INTERVAL"])
                else:
                    print(
                        f"Telegram第 {i}/{len(batches)} 批次发送失败 [{report_type}]，错误：{result.get('description')}"
                    )
                    return False
            else:
                print(
                    f"Telegram第 {i}/{len(batches)} 批次发送失败 [{report_type}]，状态码：{response.status_code}"
                )
                return False
        except Exception as e:
            print(f"Telegram第 {i}/{len(batches)} 批次发送出错 [{report_type}]：{e}")
            return False

    print(f"Telegram所有 {len(batches)} 批次发送完成 [{report_type}]")
    return True


# === 主分析器 ===
class NewsAnalyzer:
    """新闻分析器"""

    # 模式策略定义
    MODE_STRATEGIES = {
        "incremental": {
            "mode_name": "增量模式",
            "description": "增量模式（只关注新增新闻，无新增时不推送）",
            "realtime_report_type": "实时增量",
            "summary_report_type": "当日汇总",
            "should_send_realtime": True,
            "should_generate_summary": True,
            "summary_mode": "daily",
        },
        "current": {
            "mode_name": "当前榜单模式",
            "description": "当前榜单模式（当前榜单匹配新闻 + 新增新闻区域 + 按时推送）",
            "realtime_report_type": "实时当前榜单",
            "summary_report_type": "当前榜单汇总",
            "should_send_realtime": True,
            "should_generate_summary": True,
            "summary_mode": "current",
        },
        "daily": {
            "mode_name": "当日汇总模式",
            "description": "当日汇总模式（所有匹配新闻 + 新增新闻区域 + 按时推送）",
            "realtime_report_type": "",
            "summary_report_type": "当日汇总",
            "should_send_realtime": False,
            "should_generate_summary": True,
            "summary_mode": "daily",
        },
    }

    def __init__(self):
        self.request_interval = CONFIG["REQUEST_INTERVAL"]
        self.report_mode = CONFIG["REPORT_MODE"]
        self.rank_threshold = CONFIG["RANK_THRESHOLD"]
        self.is_github_actions = os.environ.get("GITHUB_ACTIONS") == "true"
        self.is_docker_container = self._detect_docker_environment()
        self.update_info = None
        self.proxy_url = None
        self._setup_proxy()
        self.data_fetcher = DataFetcher(self.proxy_url)

        if self.is_github_actions:
            self._check_version_update()

    def _detect_docker_environment(self) -> bool:
        """检测是否运行在 Docker 容器中"""
        try:
            if os.environ.get("DOCKER_CONTAINER") == "true":
                return True

            if os.path.exists("/.dockerenv"):
                return True

            return False
        except Exception:
            return False


    def _setup_proxy(self) -> None:
        """设置代理配置"""
        if not self.is_github_actions and CONFIG["USE_PROXY"]:
            self.proxy_url = CONFIG["DEFAULT_PROXY"]
            print("本地环境，使用代理")
        elif not self.is_github_actions and not CONFIG["USE_PROXY"]:
            print("本地环境，未启用代理")
        else:
            print("GitHub Actions环境，不使用代理")

    def _check_version_update(self) -> None:
        """检查版本更新"""
        try:
            need_update, remote_version = check_version_update(
                VERSION, CONFIG["VERSION_CHECK_URL"], self.proxy_url
            )

            if need_update and remote_version:
                self.update_info = {
                    "current_version": VERSION,
                    "remote_version": remote_version,
                }
                print(f"发现新版本: {remote_version} (当前: {VERSION})")
            else:
                print("版本检查完成，当前为最新版本")
        except Exception as e:
            print(f"版本检查出错: {e}")

    def _get_mode_strategy(self) -> Dict:
        """获取当前模式的策略配置"""
        return self.MODE_STRATEGIES.get(self.report_mode, self.MODE_STRATEGIES["daily"])

    def _has_webhook_configured(self) -> bool:
        """检查是否配置了webhook"""
        return any(
            [
                CONFIG["FEISHU_WEBHOOK_URL"],
                CONFIG["DINGTALK_WEBHOOK_URL"],
                CONFIG["WEWORK_WEBHOOK_URL"],
                (CONFIG["TELEGRAM_BOT_TOKEN"] and CONFIG["TELEGRAM_CHAT_ID"]),
            ]
        )

    def _has_valid_content(
            self, stats: List[Dict], new_titles: Optional[Dict] = None
    ) -> bool:
        """检查是否有有效的新闻内容"""
        if self.report_mode in ["incremental", "current"]:
            # 增量模式和current模式下，只要stats有内容就说明有匹配的新闻
            return any(stat["count"] > 0 for stat in stats)
        else:
            # 当日汇总模式下，检查是否有匹配的频率词新闻或新增新闻
            has_matched_news = any(stat["count"] > 0 for stat in stats)
            has_new_news = bool(
                new_titles and any(len(titles) > 0 for titles in new_titles.values())
            )
            return has_matched_news or has_new_news

    def _load_analysis_data(
            self,
    ) -> Optional[Tuple[Dict, Dict, Dict, Dict, List, List]]:
        """统一的数据加载和预处理，使用当前监控平台列表过滤历史数据"""
        try:
            # 获取当前配置的监控平台ID列表
            current_platform_ids = []
            for platform in CONFIG["PLATFORMS"]:
                current_platform_ids.append(platform["id"])

            print(f"当前监控平台: {current_platform_ids}")

            all_results, id_to_name, title_info = read_all_today_titles(
                current_platform_ids
            )

            if not all_results:
                print("没有找到当天的数据")
                return None

            total_titles = sum(len(titles) for titles in all_results.values())
            print(f"读取到 {total_titles} 个标题（已按当前监控平台过滤）")

            new_titles = detect_latest_new_titles(current_platform_ids)
            word_groups, filter_words = load_frequency_words()

            return (
                all_results,
                id_to_name,
                title_info,
                new_titles,
                word_groups,
                filter_words,
            )
        except Exception as e:
            print(f"数据加载失败: {e}")
            return None

    def _prepare_current_title_info(self, results: Dict, time_info: str) -> Dict:
        """从当前抓取结果构建标题信息"""
        title_info = {}
        for source_id, titles_data in results.items():
            title_info[source_id] = {}
            for title, title_data in titles_data.items():
                ranks = title_data.get("ranks", [])
                url = title_data.get("url", "")
                mobile_url = title_data.get("mobileUrl", "")

                title_info[source_id][title] = {
                    "first_time": time_info,
                    "last_time": time_info,
                    "count": 1,
                    "ranks": ranks,
                    "url": url,
                    "mobileUrl": mobile_url,
                }
        return title_info

    def _run_analysis_pipeline(
            self,
            data_source: Dict,
            mode: str,
            title_info: Dict,
            new_titles: Dict,
            word_groups: List[Dict],
            filter_words: List[str],
            id_to_name: Dict,
            failed_ids: Optional[List] = None,
            is_daily_summary: bool = False,
    ) -> Tuple[List[Dict], str]:
        """统一的分析流水线：数据处理 → 统计计算 → HTML生成"""

        # 统计计算
        stats, total_titles = count_word_frequency(
            data_source,
            word_groups,
            filter_words,
            id_to_name,
            title_info,
            self.rank_threshold,
            new_titles,
            mode=mode,
        )

        # HTML生成
        html_file = generate_html_report(
            stats,
            total_titles,
            failed_ids=failed_ids,
            new_titles=new_titles,
            id_to_name=id_to_name,
            mode=mode,
            is_daily_summary=is_daily_summary,
        )

        # 生成小红书文案
        generate_xiaohongshu_content(stats, failed_ids)

        return stats, html_file

    def _send_notification_if_needed(
            self,
            stats: List[Dict],
            report_type: str,
            mode: str,
            failed_ids: Optional[List] = None,
            new_titles: Optional[Dict] = None,
            id_to_name: Optional[Dict] = None,
    ) -> bool:
        """统一的通知发送逻辑，包含所有判断条件"""
        has_webhook = self._has_webhook_configured()

        if (
                CONFIG["ENABLE_NOTIFICATION"]
                and has_webhook
                and self._has_valid_content(stats, new_titles)
        ):
            send_to_webhooks(
                stats,
                failed_ids or [],
                report_type,
                new_titles,
                id_to_name,
                self.update_info,
                self.proxy_url,
                mode=mode,
            )
            return True
        elif CONFIG["ENABLE_NOTIFICATION"] and not has_webhook:
            print("⚠️ 警告：通知功能已启用但未配置webhook URL，将跳过通知发送")
        elif not CONFIG["ENABLE_NOTIFICATION"]:
            print(f"跳过{report_type}通知：通知功能已禁用")
        elif (
                CONFIG["ENABLE_NOTIFICATION"]
                and has_webhook
                and not self._has_valid_content(stats, new_titles)
        ):
            mode_strategy = self._get_mode_strategy()
            if "实时" in report_type:
                print(
                    f"跳过实时推送通知：{mode_strategy['mode_name']}下未检测到匹配的新闻"
                )
            else:
                print(
                    f"跳过{mode_strategy['summary_report_type']}通知：未匹配到有效的新闻内容"
                )

        return False

    def _generate_summary_report(self, mode_strategy: Dict) -> Optional[str]:
        """生成汇总报告（带通知）"""
        summary_type = (
            "当前榜单汇总" if mode_strategy["summary_mode"] == "current" else "当日汇总"
        )
        print(f"生成{summary_type}报告...")

        # 加载分析数据
        analysis_data = self._load_analysis_data()
        if not analysis_data:
            return None

        all_results, id_to_name, title_info, new_titles, word_groups, filter_words = (
            analysis_data
        )

        # 运行分析流水线
        stats, html_file = self._run_analysis_pipeline(
            all_results,
            mode_strategy["summary_mode"],
            title_info,
            new_titles,
            word_groups,
            filter_words,
            id_to_name,
            is_daily_summary=True,
        )

        print(f"{summary_type}报告已生成: {html_file}")

        # 发送通知
        self._send_notification_if_needed(
            stats,
            mode_strategy["summary_report_type"],
            mode_strategy["summary_mode"],
            new_titles=new_titles,
            id_to_name=id_to_name,
        )

        return html_file

    def _generate_summary_html(self, mode: str = "daily") -> Optional[str]:
        """生成汇总HTML"""
        summary_type = "当前榜单汇总" if mode == "current" else "当日汇总"
        print(f"生成{summary_type}HTML...")

        # 加载分析数据
        analysis_data = self._load_analysis_data()
        if not analysis_data:
            return None

        all_results, id_to_name, title_info, new_titles, word_groups, filter_words = (
            analysis_data
        )

        # 运行分析流水线
        _, html_file = self._run_analysis_pipeline(
            all_results,
            mode,
            title_info,
            new_titles,
            word_groups,
            filter_words,
            id_to_name,
            is_daily_summary=True,
        )

        print(f"{summary_type}HTML已生成: {html_file}")
        return html_file

    def _initialize_and_check_config(self) -> None:
        """通用初始化和配置检查"""
        now = get_beijing_time()
        print(f"当前北京时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        if not CONFIG["ENABLE_CRAWLER"]:
            print("爬虫功能已禁用（ENABLE_CRAWLER=False），程序退出")
            return

        has_webhook = self._has_webhook_configured()
        if not CONFIG["ENABLE_NOTIFICATION"]:
            print("通知功能已禁用（ENABLE_NOTIFICATION=False），将只进行数据抓取")
        elif not has_webhook:
            print("未配置任何webhook URL，将只进行数据抓取，不发送通知")
        else:
            print("通知功能已启用，将发送webhook通知")

        mode_strategy = self._get_mode_strategy()
        print(f"报告模式: {self.report_mode}")
        print(f"运行模式: {mode_strategy['description']}")

    def _crawl_data(self) -> Tuple[Dict, Dict, List]:
        """执行数据爬取"""
        ids = []
        for platform in CONFIG["PLATFORMS"]:
            if "name" in platform:
                ids.append((platform["id"], platform["name"]))
            else:
                ids.append(platform["id"])

        print(
            f"配置的监控平台: {[p.get('name', p['id']) for p in CONFIG['PLATFORMS']]}"
        )
        print(f"开始爬取数据，请求间隔 {self.request_interval} 毫秒")
        ensure_directory_exists("output")

        results, id_to_name, failed_ids = self.data_fetcher.crawl_websites(
            ids, self.request_interval
        )

        title_file = save_titles_to_file(results, id_to_name, failed_ids)
        print(f"标题已保存到: {title_file}")

        return results, id_to_name, failed_ids

    def _execute_mode_strategy(
            self, mode_strategy: Dict, results: Dict, id_to_name: Dict, failed_ids: List
    ) -> Optional[str]:
        """执行模式特定逻辑"""
        # 获取当前监控平台ID列表
        current_platform_ids = [platform["id"] for platform in CONFIG["PLATFORMS"]]

        new_titles = detect_latest_new_titles(current_platform_ids)
        time_info = Path(save_titles_to_file(results, id_to_name, failed_ids)).stem
        word_groups, filter_words = load_frequency_words()

        # current模式下，实时推送需要使用完整的历史数据来保证统计信息的完整性
        if self.report_mode == "current":
            # 加载完整的历史数据（已按当前平台过滤）
            analysis_data = self._load_analysis_data()
            if analysis_data:
                (
                    all_results,
                    historical_id_to_name,
                    historical_title_info,
                    historical_new_titles,
                    _,
                    _,
                ) = analysis_data

                print(
                    f"current模式：使用过滤后的历史数据，包含平台：{list(all_results.keys())}"
                )

                stats, html_file = self._run_analysis_pipeline(
                    all_results,
                    self.report_mode,
                    historical_title_info,
                    historical_new_titles,
                    word_groups,
                    filter_words,
                    historical_id_to_name,
                    failed_ids=failed_ids,
                )

                combined_id_to_name = {**historical_id_to_name, **id_to_name}

                print(f"HTML报告已生成: {html_file}")

                # 发送实时通知（使用完整历史数据的统计结果）
                summary_html = None
                if mode_strategy["should_send_realtime"]:
                    self._send_notification_if_needed(
                        stats,
                        mode_strategy["realtime_report_type"],
                        self.report_mode,
                        failed_ids=failed_ids,
                        new_titles=historical_new_titles,
                        id_to_name=combined_id_to_name,
                    )
            else:
                print("❌ 严重错误：无法读取刚保存的数据文件")
                raise RuntimeError("数据一致性检查失败：保存后立即读取失败")
        else:
            title_info = self._prepare_current_title_info(results, time_info)
            stats, html_file = self._run_analysis_pipeline(
                results,
                self.report_mode,
                title_info,
                new_titles,
                word_groups,
                filter_words,
                id_to_name,
                failed_ids=failed_ids,
            )
            print(f"HTML报告已生成: {html_file}")

            # 发送实时通知（如果需要）
            summary_html = None
            if mode_strategy["should_send_realtime"]:
                self._send_notification_if_needed(
                    stats,
                    mode_strategy["realtime_report_type"],
                    self.report_mode,
                    failed_ids=failed_ids,
                    new_titles=new_titles,
                    id_to_name=id_to_name,
                )

        # 生成汇总报告（如果需要）
        summary_html = None
        if mode_strategy["should_generate_summary"]:
            if mode_strategy["should_send_realtime"]:
                # 如果已经发送了实时通知，汇总只生成HTML不发送通知
                summary_html = self._generate_summary_html(
                    mode_strategy["summary_mode"]
                )
            else:
                # daily模式：直接生成汇总报告并发送通知
                summary_html = self._generate_summary_report(mode_strategy)

        if self.is_docker_container and html_file:
            if summary_html:
                print(f"汇总报告已生成: {summary_html}")
            else:
                print(f"HTML报告已生成: {html_file}")

        return summary_html

    def run(self) -> None:
        """执行分析流程"""
        try:
            self._initialize_and_check_config()

            mode_strategy = self._get_mode_strategy()

            results, id_to_name, failed_ids = self._crawl_data()

            summary_html_path = self._execute_mode_strategy(
                mode_strategy, results, id_to_name, failed_ids
            )

            # 运行结束后，生成静态API文件和关联的图片
            generate_static_api_files(self)

        except Exception as e:
            print(f"分析流程执行出错: {e}")
            raise


# === API 功能部分 ===

# 沿用旧版的固定ID列表用于API生成
API_IDS = [
    ("toutiao", "今日头条"), ("baidu", "百度热搜"), ("wallstreetcn-hot", "华尔街见闻"),
    ("thepaper", "澎湃新闻"), ("bilibili-hot-search", "bilibili 热搜"), ("cls-hot", "财联社热门"),
    ("ifeng", "凤凰网"), ("jin10", "金十数据"), ("wallstreetcn-quick", "华尔街见闻-快讯"),
    ("tieba", "贴吧"), ("weibo", "微博"), ("douyin", "抖音"), ("zhihu", "知乎"),
]


def generate_api_data(
    analyzer: "NewsAnalyzer",
) -> Tuple[Dict, List, int, List, Dict]:
    """
    获取并分析来自固定源的趋势数据，返回API所需的所有数据。
    """
    print("为API生成数据：开始获取和分析...")

    # 1. 爬取数据
    results, id_to_name, failed_ids = analyzer.data_fetcher.crawl_websites(
        API_IDS, analyzer.request_interval
    )

    # 2. 保存原始数据（可选，但保持与主流程一致）
    save_titles_to_file(results, id_to_name, failed_ids)

    # 3. 分析数据
    api_id_list = [
        item[0] if isinstance(item, tuple) else item for item in API_IDS
    ]
    all_results, final_id_to_name, title_info = read_all_today_titles(api_id_list)

    if not all_results:
        empty_response = {
            "generated_at": get_beijing_time().isoformat(),
            "total_titles_processed": 0,
            "failed_sources": failed_ids,
            "trends": [],
        }
        return empty_response, [], 0, failed_ids, {}

    new_titles = detect_latest_new_titles(api_id_list)
    word_groups, filter_words = load_frequency_words()

    stats, total_titles = count_word_frequency(
        all_results,
        word_groups,
        filter_words,
        final_id_to_name,
        title_info,
        analyzer.rank_threshold,
        new_titles,
        mode="daily",  # API通常提供当日汇总数据
    )

    # 4. 格式化为API响应结构
    api_response = {
        "generated_at": get_beijing_time().isoformat(),
        "total_titles_processed": total_titles,
        "failed_sources": failed_ids,
        "trends": [],
    }

    for stat in stats:
        if stat["count"] > 0:
            trend_item = {
                "keyword_group": stat["word"],
                "match_count": stat["count"],
                "titles": [],
            }
            for title_data in stat["titles"]:
                trend_item["titles"].append(
                    {
                        "title": clean_title(title_data["title"]),
                        "url": title_data.get("mobileUrl") or title_data.get("url"),
                        "source": title_data.get("source_name"),
                        "ranks": title_data.get("ranks", []),
                        "is_new": title_data.get("is_new", False),
                        "appearance_count": title_data.get("count", 1),
                        "time_info": title_data.get("time_display", ""),
                    }
                )
            api_response["trends"].append(trend_item)

    return api_response, stats, total_titles, failed_ids, final_id_to_name


def generate_static_api_files(analyzer: "NewsAnalyzer"):
    """
    获取趋势数据，生成HTML报告和图片，并将其保存为静态的 JSON 文件。
    """
    (
        api_data,
        stats,
        total_titles,
        failed_ids,
        id_to_name,
    ) = generate_api_data(analyzer)

    # 生成与API数据关联的HTML报告
    api_html_report_path = generate_html_report(
        stats,
        total_titles,
        failed_ids,
        id_to_name=id_to_name,
        mode="daily",
        is_daily_summary=True,
    )
    print(f"为API数据生成了HTML报告: {api_html_report_path}")

    # 确保API目录存在并将JSON文件保存到新路径
    output_path = "api/trends.json"
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(api_data, f, ensure_ascii=False, indent=2)

    print(f"静态API文件已成功生成: {output_path}")


# --- Flask App (如果已安装) ---
if FLASK_AVAILABLE:
    app = Flask(__name__)

    @app.route('/api/trends.json')
    @app.route('/api/trends')
    def get_trends():
        """
        API端点，实时生成并返回趋势数据。
        注意：这是一个耗时操作，每次请求都会重新爬取、分析和渲染图片。
        """
        try:
            analyzer = NewsAnalyzer()
            # 运行完整的静态文件生成流程
            generate_static_api_files(analyzer)
            # 读取刚刚生成的文件并返回
            api_file = Path("api/trends.json")
            if api_file.exists():
                with open(api_file, "r", encoding="utf-8") as f:
                    return jsonify(json.load(f))
            else:
                return jsonify({"error": "API文件生成失败"}), 500
        except Exception as e:
            print(f"API请求处理失败: {e}")
            return jsonify({"error": "内部服务器错误", "message": str(e)}), 500

    @app.route('/img/<path:filename>')
    def serve_image(filename):
        """提供图片文件的API端点"""
        return send_from_directory('img', filename)


def main():
    parser = argparse.ArgumentParser(description="TrendRadar: 新闻热点分析工具。")
    parser.add_argument(
        '--serve-api',
        action='store_true',
        help='以API服务器模式运行，监听在 http://0.0.0.0:5001'
    )
    parser.add_argument(
        '--generate-json',
        action='store_true',
        help='仅生成静态的 trends.json, news.jpg 和相关HTML文件并退出'
    )
    args = parser.parse_args()

    try:
        if args.serve_api:
            if not FLASK_AVAILABLE:
                print("错误：无法启动API服务器，因为 Flask 模块未安装。")
                print("请运行 'pip install Flask' 来安装。")
                return
            print("以API服务器模式启动...")
            app.run(host='0.0.0.0', port=5001, debug=False)

        elif args.generate_json:
            print("仅生成静态API文件...")
            analyzer = NewsAnalyzer()
            generate_static_api_files(analyzer)
            print("文件生成完毕。")

        else:
            print("以单次脚本模式运行...")
            analyzer = NewsAnalyzer()
            analyzer.run()

    except FileNotFoundError as e:
        print(f"❌ 配置文件错误: {e}")
        print("\n请确保以下文件存在:")
        print("  • config/config.yaml")
        print("  • config/frequency_words.txt")
        print("\n参考项目文档进行正确配置")
    except Exception as e:
        print(f"❌ 程序运行错误: {e}")
        raise


if __name__ == "__main__":
    main()
