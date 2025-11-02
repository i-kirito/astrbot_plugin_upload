"""
CodeMage - AI驱动的AstrBot插件生成器
根据用户描述自动生成AstrBot插件
"""

import os
import json
import asyncio
import hashlib
from typing import Optional, Dict, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .llm_handler import LLMHandler
from .plugin_generator import PluginGenerator
from .directory_detector import DirectoryDetector
from .installer import PluginInstaller
from .utils import validate_plugin_description, format_plugin_info


@register(
    "astrbot_plugin_codemage",
    "qa296",
    "AI驱动的AstrBot插件生成器",
    "1.0.0",
    "https://github.com/qa296/astrbot_plugin_codemage",
)
class CodeMagePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.llm_handler = LLMHandler(context, config)
        self.installer = PluginInstaller(config)
        self.plugin_generator = PluginGenerator(context, config, self.installer)
        self.directory_detector = DirectoryDetector()

        # 初始化logger
        self.logger = logger

        # 验证配置
        self._validate_config()

    def _validate_config(self):
        """验证配置文件"""
        if not self.config.get("llm_provider_id"):
            self.logger.warning("未配置LLM提供商ID，请检查配置")

    def _check_admin_permission(self, event: AstrMessageEvent) -> bool:
        """检查管理员权限

        Args:
            event: 消息事件

        Returns:
            bool: 是否有管理员权限
        """
        if not self.config.get("admin_only", True):
            return True

        # 获取发送者ID
        sender_id = event.get_sender_id()

        # 尝试从context获取管理员列表
        try:
            astrbot_config = self.context.get_config()
            admins = astrbot_config.get("admins", [])
            return sender_id in admins
        except Exception as e:
            self.logger.warning(f"无法检查管理员权限: {str(e)}")
            # 如果无法检查，默认允许（防止插件不可用）
            return True

    @filter.command("生成插件", alias={"create_plugin", "new_plugin"})
    async def generate_plugin_command(
        self, event: AstrMessageEvent, plugin_description: str = ""
    ):
        """生成AstrBot插件指令

        Args:
            plugin_description(string): 插件功能描述
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("⚠️ 仅管理员可以使用此功能")
            return

        if not plugin_description:
            yield event.plain_result(
                "请提供插件描述，例如：/生成插件 创建一个天气查询插件"
            )
            return

        # 验证描述
        if not validate_plugin_description(plugin_description):
            yield event.plain_result("插件描述不合适，请重新描述")
            return

        # 开始生成流程
        try:
            yield event.plain_result("开始生成插件，请稍候...")
            result = await self.plugin_generator.generate_plugin_flow(
                plugin_description, event
            )

            if result["success"]:
                message = f"插件生成成功！\n插件名称：{result['plugin_name']}\n插件路径：{result['plugin_path']}"
                if result.get("installed"):
                    message += f"\n安装状态：{'✅ 已安装' if result.get('install_success') else '❌ 安装失败'}"
                    if not result.get("install_success"):
                        message += (
                            f"\n安装错误：{result.get('install_error', '未知错误')}"
                        )
                yield event.plain_result(message)
            else:
                yield event.plain_result(f"插件生成失败：{result['error']}")

        except Exception as e:
            self.logger.error(f"插件生成过程中发生错误: {str(e)}")
            yield event.plain_result(f"插件生成失败：{str(e)}")

    @filter.command("插件生成状态", alias={"plugin_status"})
    async def plugin_status(self, event: AstrMessageEvent):
        """查看插件生成器状态"""
        # 获取当前生成状态
        current_status = self.plugin_generator.get_current_status()

        # 当前生成步骤信息
        if current_status["is_generating"]:
            status_info = f"""
当前插件生成状态：
- 正在生成：{"是" if current_status["is_generating"] else "否"}
- 当前步骤：{current_status["current_step"]}
- 总步骤：{current_status["total_steps"]}
- 进度：{current_status["progress_percentage"]}%
- 插件名称：{current_status.get("plugin_name", "未知")}
- 开始时间：{current_status.get("start_time", "未知")}
            """.strip()
        else:
            status_info = "当前没有正在进行的插件生成任务"

        yield event.plain_result(status_info)

    @filter.llm_tool(name="generate_plugin")
    async def generate_plugin_tool(
        self, event: AstrMessageEvent, plugin_description: str
    ) -> Dict[str, Any]:
        """通过函数调用生成插件

        Args:
            plugin_description(string): 插件功能描述

        Returns:
            dict: 生成结果
        """
        if not self.config.get("enable_function_call", True):
            return {"error": "函数调用未启用"}

        # 检查管理员权限
        if not self._check_admin_permission(event):
            return {"error": "仅管理员可以使用此功能"}

        try:
            result = await self.plugin_generator.generate_plugin_flow(
                plugin_description, event
            )
            return result
        except Exception as e:
            self.logger.error(f"函数调用生成插件失败: {str(e)}")
            return {"error": str(e)}

    @filter.command("密码转md5")
    async def md5_convert(self, event: AstrMessageEvent, password: str = ""):
        """将明文密码转换为MD5加密密码

        Args:
            password(string): 明文密码
        """
        if not password:
            yield event.plain_result("请提供要转换的密码，例如：/密码转md5 astrbot")
            return

        try:
            md5_password = hashlib.md5(password.encode()).hexdigest()
            result_message = f"MD5转换结果：\n明文密码：{password}\nMD5密码：{md5_password}\n\n请将MD5密码复制到插件配置中的 api_password_md5 字段"
            yield event.plain_result(result_message)
        except Exception as e:
            self.logger.error(f"MD5转换失败: {str(e)}")
            yield event.plain_result(f"MD5转换失败：{str(e)}")

    @filter.command("同意生成", alias={"同意", "approve", "confirm"})
    async def approve_generation(self, event: AstrMessageEvent, feedback: str = ""):
        """同意插件生成指令
        
        Args:
            feedback(string): 可选的修改反馈
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("⚠️ 仅管理员可以使用此功能")
            return
            
        # 获取待确认的任务
        pending = self.plugin_generator.get_pending_generation()
        if not pending["active"]:
            yield event.plain_result("当前没有待确认的插件生成任务")
            return
            
        # 继续插件生成流程
        try:
            yield event.plain_result("正在继续插件生成流程...")
            result = await self.plugin_generator.continue_plugin_generation(True, feedback)
            
            if result["success"]:
                message = f"插件生成成功！\n插件名称：{result['plugin_name']}\n插件路径：{result['plugin_path']}"
                if result.get("installed"):
                    message += f"\n安装状态：{'✅ 已安装' if result.get('install_success') else '❌ 安装失败'}"
                    if not result.get("install_success"):
                        message += (
                            f"\n安装错误：{result.get('install_error', '未知错误')}"
                        )
                yield event.plain_result(message)
            else:
                if not result.get("pending_confirmation"):
                    yield event.plain_result(f"插件生成失败：{result['error']}")
        except Exception as e:
            self.logger.error(f"同意插件生成过程中发生错误: {str(e)}")
            yield event.plain_result(f"插件生成失败：{str(e)}")

    @filter.command("拒绝生成", alias={"拒绝", "reject", "cancel"})
    async def reject_generation(self, event: AstrMessageEvent):
        """拒绝插件生成指令
        
        Args:
            无参数
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("⚠️ 仅管理员可以使用此功能")
            return
            
        # 获取待确认的任务
        pending = self.plugin_generator.get_pending_generation()
        if not pending["active"]:
            yield event.plain_result("当前没有待确认的插件生成任务")
            return
            
        # 取消插件生成流程
        try:
            result = await self.plugin_generator.continue_plugin_generation(False)
            yield event.plain_result("已取消插件生成")
        except Exception as e:
            self.logger.error(f"拒绝插件生成过程中发生错误: {str(e)}")
            yield event.plain_result(f"取消插件生成失败：{str(e)}")

    async def terminate(self):
        """插件卸载时调用"""
        self.logger.info("CodeMage插件已卸载")
