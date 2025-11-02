"""
CodeMage插件生成器模块
负责协调整个插件生成流程
"""

import os
import json
import time
import asyncio
from typing import Dict, Any, Optional
from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent

from .llm_handler import LLMHandler
from .directory_detector import DirectoryDetector
from .utils import (
    sanitize_plugin_name, 
    create_plugin_directory,
    format_time
)


class PluginGenerator:
    """插件生成器类"""
    
    def __init__(self, context: Context, config: AstrBotConfig, installer=None):
        self.context = context
        self.config = config
        self.llm_handler = LLMHandler(context, config)
        self.directory_detector = DirectoryDetector()
        self.installer = installer
        self.logger = logger
        
        # 生成状态
        self.generation_status = {
            "is_generating": False,
            "current_step": 0,
            "total_steps": 6,
            "progress_percentage": 0,
            "plugin_name": "",
            "start_time": "",
            "step_descriptions": [
                "生成插件元数据",
                "生成插件文档",
                "生成配置文件",
                "生成插件代码",
                "代码审查与修复",
                "打包并安装插件"
            ]
        }
        
        # 待确认的插件生成任务
        self.pending_generation = {
            "active": False,
            "metadata": {},
            "markdown": "",
            "description": "",
            "event": None,
            "timestamp": ""
        }
        
    def get_current_status(self) -> Dict[str, Any]:
        """获取当前生成状态
        
        Returns:
            Dict[str, Any]: 当前状态
        """
        return self.generation_status.copy()
        
    def _update_status(self, step: int, plugin_name: str = ""):
        """更新生成状态
        
        Args:
            step: 当前步骤
            plugin_name: 插件名称
        """
        self.generation_status["current_step"] = step
        self.generation_status["progress_percentage"] = int((step / self.generation_status["total_steps"]) * 100)
        if plugin_name:
            self.generation_status["plugin_name"] = plugin_name
            
    async def generate_plugin_flow(self, description: str, event: AstrMessageEvent) -> Dict[str, Any]:
        """执行完整的插件生成流程
        
        Args:
            description: 插件描述
            event: 消息事件
            
        Returns:
            Dict[str, Any]: 生成结果
        """
        # 检查是否正在生成
        if self.generation_status["is_generating"]:
            return {
                "success": False,
                "error": "已有插件正在生成中，请稍后再试"
            }
            
        # 设置生成状态
        self.generation_status["is_generating"] = True
        self.generation_status["start_time"] = format_time(time.time())
        
        def build_preview_text(meta: Dict[str, Any], markdown: str) -> str:
            lines = [
                f"插件名称：{meta.get('name', '未知')}",
                f"作者：{meta.get('author', '未知')}",
                f"描述：{meta.get('description', '无描述')}",
                f"版本：{meta.get('version', '1.0.0')}"
            ]
            commands = meta.get("commands", [])
            if isinstance(commands, list) and commands:
                lines.append("指令预览：")
                for cmd in commands[:5]:
                    if isinstance(cmd, dict):
                        cmd_name = cmd.get("command") or cmd.get("name") or cmd.get("title") or "未知指令"
                        cmd_desc = cmd.get("description") or cmd.get("desc") or ""
                        lines.append(f"  - {cmd_name}: {cmd_desc}")
                    else:
                        lines.append(f"  - {cmd}")
            doc_preview = (markdown or "").strip()
            if doc_preview:
                snippet = doc_preview[:400] + ("..." if len(doc_preview) > 400 else "")
                lines.append("\nMarkdown文档预览：")
                lines.append(snippet)
            return "\n".join(lines)
        
        def normalize_review_result(result: Dict[str, Any]) -> Dict[str, Any]:
            approved = result.get("approved")
            if approved is None:
                approved = result.get("是否同意") or result.get("agree")
            if isinstance(approved, str):
                approved = approved.strip().lower() in {"true", "yes", "同意", "通过", "approved"}
            result["approved"] = bool(approved)
            satisfaction = result.get("satisfaction_score")
            if satisfaction is None:
                satisfaction = result.get("满意分数") or result.get("score")
            try:
                satisfaction = int(float(satisfaction))
            except (TypeError, ValueError):
                satisfaction = 0
            result["satisfaction_score"] = satisfaction
            reason = result.get("reason") or result.get("理由") or ""
            result["reason"] = reason
            issues = result.get("issues") or result.get("问题") or []
            if isinstance(issues, str):
                issues = [issues]
            if not isinstance(issues, list):
                issues = [str(issues)]
            if not issues and reason:
                issues = [reason]
            result["issues"] = issues
            suggestions = result.get("suggestions") or result.get("建议") or []
            if isinstance(suggestions, str):
                suggestions = [suggestions]
            if not isinstance(suggestions, list):
                suggestions = [str(suggestions)]
            if not suggestions and reason:
                suggestions = ["请根据以下理由修复问题：" + reason]
            result["suggestions"] = suggestions
            return result
        
        try:
            # 验证目录结构
            dir_validation = self.directory_detector.validate_directory_structure()
            if not dir_validation["valid"]:
                message = f"目录结构验证失败：{'; '.join(dir_validation['issues'])}"
                await event.send(event.plain_result(message))
                self.logger.error(message)
                return {
                    "success": False,
                    "error": message
                }
                
            step_by_step = self.config.get("step_by_step", True)
            metadata: Dict[str, Any] = {}
            markdown_doc = ""
            
            # 步骤1：生成插件元数据
            self._update_status(1)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][0]}"))
            try:
                if step_by_step:
                    metadata = await self.llm_handler.generate_metadata_structure(description)
                else:
                    metadata = await self.llm_handler.generate_plugin_metadata(description)
                    markdown_doc = metadata.get("markdown", "")
            except Exception as generate_err:
                error_msg = f"生成插件元数据失败：{str(generate_err)}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            
            if not isinstance(metadata, dict):
                error_msg = "LLM返回的插件元数据格式不正确"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            
            metadata.setdefault("metadata", {})
            metadata.setdefault("commands", [])
            plugin_name = sanitize_plugin_name(metadata.get("name", "astrbot_plugin_generated"))
            if not plugin_name.startswith("astrbot_plugin_"):
                plugin_name = f"astrbot_plugin_{plugin_name}"
            metadata["name"] = plugin_name
            self._update_status(1, plugin_name)
            
            # 检查插件是否已存在
            if self.directory_detector.check_plugin_exists(plugin_name):
                message = f"插件 '{plugin_name}' 已存在"
                await event.send(event.plain_result(message))
                self.logger.warning(message)
                return {
                    "success": False,
                    "error": message
                }
                
            # 步骤2：生成插件文档
            self._update_status(2, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][1]}"))
            try:
                if step_by_step or not markdown_doc:
                    markdown_doc = await self.llm_handler.generate_markdown_document(metadata, description)
            except Exception as doc_err:
                error_msg = f"生成插件文档失败：{str(doc_err)}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            metadata["markdown"] = markdown_doc
            await event.send(event.plain_result(f"初步生成的插件方案：\n\n{build_preview_text(metadata, markdown_doc)}"))
            
            # 用户确认 - 修改为指令方式
            if not self.config.get("auto_approve", False):
                # 保存待确认的任务信息
                self.pending_generation = {
                    "active": True,
                    "metadata": metadata,
                    "markdown": markdown_doc,
                    "description": description,
                    "event": event,
                    "timestamp": format_time(time.time())
                }
                
                await event.send(event.plain_result("请使用指令 '/同意生成' 确认生成，或 '/拒绝生成' 取消生成。"))
                return {
                    "success": False,
                    "error": "等待用户确认",
                    "pending_confirmation": True
                }
            else:
                await event.send(event.plain_result("根据配置已自动批准插件方案。"))
            
            # 步骤3：生成插件代码
            self._update_status(3, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][2]}"))
            self.logger.info(f"开始生成插件代码: {plugin_name}")
            try:
                code = await self.llm_handler.generate_plugin_code(metadata, markdown_doc)
            except Exception as code_err:
                error_msg = f"生成插件代码失败：{str(code_err)}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            
            # 步骤4：代码审查与修复
            self._update_status(4, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][3]}"))
            self.logger.info(f"开始代码审查: {plugin_name}")
            review_result = normalize_review_result(await self._review_code_with_retry(code, metadata, markdown_doc))
            satisfaction_threshold = self.config.get("satisfaction_threshold", 80)
            strict_review = self.config.get("strict_review", True)
            max_retries = self.config.get("max_retries", 3)
            unlimited_retry = max_retries == -1
            retry_count = 0
            
            while ((review_result["satisfaction_score"] < satisfaction_threshold) or (not review_result["approved"])) and (unlimited_retry or retry_count < max_retries):
                retry_count += 1
                if strict_review and not review_result["approved"]:
                    await event.send(event.plain_result(f"代码审查未通过，正在修复（第{retry_count}次重试）..."))
                else:
                    await event.send(event.plain_result(f"代码满意度不足（{review_result['satisfaction_score']}分），正在优化（第{retry_count}次重试）..."))
                code = await self.llm_handler.fix_plugin_code(code, review_result["issues"], review_result["suggestions"])
                review_result = normalize_review_result(await self.llm_handler.review_plugin_code(code, metadata, markdown_doc))
            
            if (review_result["satisfaction_score"] < satisfaction_threshold) or (not review_result["approved"]):
                reason = review_result.get("reason", "代码审查未通过")
                return {
                    "success": False,
                    "error": f"代码审查未通过：{reason}"
                }
            
            await event.send(event.plain_result(f"代码审查通过，满意度得分：{review_result['satisfaction_score']}分"))
            
            # 步骤5：生成最终插件并安装
            self._update_status(5, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][4]}"))
            
            plugin_path = await self._create_plugin_files(plugin_name, metadata, code, markdown_doc)
            self.logger.info(f"插件生成成功: {plugin_name} -> {plugin_path}")
            
            result = {
                "success": True,
                "plugin_name": plugin_name,
                "plugin_path": plugin_path,
                "satisfaction_score": review_result["satisfaction_score"],
                "installed": False
            }
            
            # 尝试安装插件（如果配置了installer）
            if self.installer and self.config.get("api_password_md5"):
                await event.send(event.plain_result("正在通过API安装插件..."))
                try:
                    zip_path = await self.installer.create_plugin_zip(plugin_path)
                    if zip_path:
                        install_result = await self.installer.install_plugin(zip_path)
                        result["installed"] = True
                        result["install_success"] = install_result.get("success", False)
                        if install_result.get("success"):
                            await event.send(event.plain_result("✅ 插件已通过API安装"))
                            status_check = await self.installer.check_plugin_install_status(plugin_name)
                            if status_check.get("has_errors"):
                                error_msg = "⚠️ 插件安装后检测到错误：\n" + "\n".join(status_check.get("error_logs", []))
                                await event.send(event.plain_result(error_msg))
                        else:
                            result["install_error"] = install_result.get("error", "未知错误")
                            await event.send(event.plain_result(f"❌ 插件安装失败: {install_result.get('error')}"))
                        import os
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                except Exception as e:
                    self.logger.error(f"安装插件失败: {str(e)}")
                    result["install_error"] = str(e)
                    await event.send(event.plain_result(f"❌ 安装插件失败: {str(e)}"))
            else:
                self.logger.info("未配置API密码或installer，跳过自动安装")
            
            return result
            
        except Exception as e:
            self.logger.error(f"插件生成流程失败：{str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            self.generation_status["is_generating"] = False
            self.generation_status["current_step"] = 0
            self.generation_status["progress_percentage"] = 0
            self.generation_status["plugin_name"] = ""
            
    def get_pending_generation(self) -> Dict[str, Any]:
        """获取待确认的插件生成任务
        
        Returns:
            Dict[str, Any]: 待确认任务信息
        """
        return self.pending_generation.copy()
        
    def clear_pending_generation(self):
        """清除待确认的插件生成任务"""
        self.pending_generation = {
            "active": False,
            "metadata": {},
            "markdown": "",
            "description": "",
            "event": None,
            "timestamp": ""
        }
        
    async def continue_plugin_generation(self, approved: bool, feedback: str = "") -> Dict[str, Any]:
        """继续插件生成流程（用于指令确认）
        
        Args:
            approved: 是否同意生成
            feedback: 用户反馈（如果有）
            
        Returns:
            Dict[str, Any]: 生成结果
        """
        if not self.pending_generation["active"]:
            return {
                "success": False,
                "error": "没有待确认的插件生成任务"
            }
            
        # 获取保存的任务信息
        metadata = self.pending_generation["metadata"]
        markdown_doc = self.pending_generation["markdown"]
        description = self.pending_generation["description"]
        event = self.pending_generation["event"]
        
        # 清除待确认任务
        self.clear_pending_generation()
        
        if not approved:
            await event.send(event.plain_result("用户取消了插件生成"))
            return {
                "success": False,
                "error": "用户取消了插件生成"
            }
            
        # 如果有反馈，先优化插件方案
        if feedback:
            await event.send(event.plain_result("正在根据您的反馈优化插件方案..."))
            self.logger.info(f"根据用户反馈优化插件设计: {feedback}")
            try:
                metadata = await self.llm_handler.optimize_plugin_metadata(metadata, feedback)
                if not isinstance(metadata, dict):
                    raise ValueError("LLM返回的优化结果格式不正确")
                metadata.setdefault("metadata", {})
                metadata.setdefault("commands", [])
                markdown_doc = metadata.get("markdown", markdown_doc)
                plugin_name = sanitize_plugin_name(metadata.get("name", metadata.get("name", "generated_plugin")))
                if not plugin_name.startswith("astrbot_plugin_"):
                    plugin_name = f"astrbot_plugin_{plugin_name}"
                metadata["name"] = plugin_name
                metadata["markdown"] = markdown_doc
                
                # 检查插件是否已存在
                if self.directory_detector.check_plugin_exists(plugin_name):
                    message = f"插件 '{plugin_name}' 已存在"
                    await event.send(event.plain_result(message))
                    self.logger.warning(message)
                    return {
                        "success": False,
                        "error": message
                    }
                    
                # 显示优化后的方案
                def build_preview_text(meta: Dict[str, Any], markdown: str) -> str:
                    lines = [
                        f"插件名称：{meta.get('name', '未知')}",
                        f"作者：{meta.get('author', '未知')}",
                        f"描述：{meta.get('description', '无描述')}",
                        f"版本：{meta.get('version', '1.0.0')}"
                    ]
                    commands = meta.get("commands", [])
                    if isinstance(commands, list) and commands:
                        lines.append("指令预览：")
                        for cmd in commands[:5]:
                            if isinstance(cmd, dict):
                                cmd_name = cmd.get("command") or cmd.get("name") or cmd.get("title") or "未知指令"
                                cmd_desc = cmd.get("description") or cmd.get("desc") or ""
                                lines.append(f"  - {cmd_name}: {cmd_desc}")
                            else:
                                lines.append(f"  - {cmd}")
                    doc_preview = (markdown or "").strip()
                    if doc_preview:
                        snippet = doc_preview[:400] + ("..." if len(doc_preview) > 400 else "")
                        lines.append("\nMarkdown文档预览：")
                        lines.append(snippet)
                    return "\n".join(lines)
                    
                await event.send(event.plain_result(f"优化后的插件方案：\n\n{build_preview_text(metadata, markdown_doc)}"))
            except Exception as e:
                self.logger.error(f"优化插件方案失败: {str(e)}")
                return {
                    "success": False,
                    "error": f"优化插件方案失败：{str(e)}"
                }
        
        # 继续执行生成流程的剩余步骤
        try:
            # 设置生成状态
            self.generation_status["is_generating"] = True
            self.generation_status["start_time"] = format_time(time.time())
            
            plugin_name = sanitize_plugin_name(metadata.get("name", "astrbot_plugin_generated"))
            if not plugin_name.startswith("astrbot_plugin_"):
                plugin_name = f"astrbot_plugin_{plugin_name}"
            metadata["name"] = plugin_name
            self._update_status(3, plugin_name)
            
            # 步骤3：生成插件代码
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][2]}"))
            self.logger.info(f"开始生成插件代码: {plugin_name}")
            try:
                code = await self.llm_handler.generate_plugin_code(metadata, markdown_doc)
            except Exception as code_err:
                error_msg = f"生成插件代码失败：{str(code_err)}"
                self.logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg
                }
            
            # 步骤4：代码审查与修复
            self._update_status(4, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][3]}"))
            self.logger.info(f"开始代码审查: {plugin_name}")
            
            def normalize_review_result(result: Dict[str, Any]) -> Dict[str, Any]:
                approved = result.get("approved")
                if approved is None:
                    approved = result.get("是否同意") or result.get("agree")
                if isinstance(approved, str):
                    approved = approved.strip().lower() in {"true", "yes", "同意", "通过", "approved"}
                result["approved"] = bool(approved)
                satisfaction = result.get("satisfaction_score")
                if satisfaction is None:
                    satisfaction = result.get("满意分数") or result.get("score")
                try:
                    satisfaction = int(float(satisfaction))
                except (TypeError, ValueError):
                    satisfaction = 0
                result["satisfaction_score"] = satisfaction
                reason = result.get("reason") or result.get("理由") or ""
                result["reason"] = reason
                issues = result.get("issues") or result.get("问题") or []
                if isinstance(issues, str):
                    issues = [issues]
                if not isinstance(issues, list):
                    issues = [str(issues)]
                if not issues and reason:
                    issues = [reason]
                result["issues"] = issues
                suggestions = result.get("suggestions") or result.get("建议") or []
                if isinstance(suggestions, str):
                    suggestions = [suggestions]
                if not isinstance(suggestions, list):
                    suggestions = [str(suggestions)]
                if not suggestions and reason:
                    suggestions = ["请根据以下理由修复问题：" + reason]
                result["suggestions"] = suggestions
                return result
                
            review_result = normalize_review_result(await self._review_code_with_retry(code, metadata, markdown_doc))
            satisfaction_threshold = self.config.get("satisfaction_threshold", 80)
            strict_review = self.config.get("strict_review", True)
            max_retries = self.config.get("max_retries", 3)
            unlimited_retry = max_retries == -1
            retry_count = 0
            
            while ((review_result["satisfaction_score"] < satisfaction_threshold) or (not review_result["approved"])) and (unlimited_retry or retry_count < max_retries):
                retry_count += 1
                if strict_review and not review_result["approved"]:
                    await event.send(event.plain_result(f"代码审查未通过，正在修复（第{retry_count}次重试）..."))
                else:
                    await event.send(event.plain_result(f"代码满意度不足（{review_result['satisfaction_score']}分），正在优化（第{retry_count}次重试）..."))
                code = await self.llm_handler.fix_plugin_code(code, review_result["issues"], review_result["suggestions"])
                review_result = normalize_review_result(await self.llm_handler.review_plugin_code(code, metadata, markdown_doc))
            
            if (review_result["satisfaction_score"] < satisfaction_threshold) or (not review_result["approved"]):
                reason = review_result.get("reason", "代码审查未通过")
                return {
                    "success": False,
                    "error": f"代码审查未通过：{reason}"
                }
            
            await event.send(event.plain_result(f"代码审查通过，满意度得分：{review_result['satisfaction_score']}分"))
            
            # 步骤5：生成最终插件并安装
            self._update_status(5, plugin_name)
            await event.send(event.plain_result(f"步骤{self.generation_status['current_step']}/{self.generation_status['total_steps']}：{self.generation_status['step_descriptions'][4]}"))
            
            plugin_path = await self._create_plugin_files(plugin_name, metadata, code, markdown_doc)
            self.logger.info(f"插件生成成功: {plugin_name} -> {plugin_path}")
            
            result = {
                "success": True,
                "plugin_name": plugin_name,
                "plugin_path": plugin_path,
                "satisfaction_score": review_result["satisfaction_score"],
                "installed": False
            }
            
            # 尝试安装插件（如果配置了installer）
            if self.installer and self.config.get("api_password_md5"):
                await event.send(event.plain_result("正在通过API安装插件..."))
                try:
                    zip_path = await self.installer.create_plugin_zip(plugin_path)
                    if zip_path:
                        install_result = await self.installer.install_plugin(zip_path)
                        result["installed"] = True
                        result["install_success"] = install_result.get("success", False)
                        if install_result.get("success"):
                            await event.send(event.plain_result("✅ 插件已通过API安装"))
                            status_check = await self.installer.check_plugin_install_status(plugin_name)
                            if status_check.get("has_errors"):
                                error_msg = "⚠️ 插件安装后检测到错误：\n" + "\n".join(status_check.get("error_logs", []))
                                await event.send(event.plain_result(error_msg))
                        else:
                            result["install_error"] = install_result.get("error", "未知错误")
                            await event.send(event.plain_result(f"❌ 插件安装失败: {install_result.get('error')}"))
                        import os
                        if os.path.exists(zip_path):
                            os.remove(zip_path)
                except Exception as e:
                    self.logger.error(f"安装插件失败: {str(e)}")
                    result["install_error"] = str(e)
                    await event.send(event.plain_result(f"❌ 安装插件失败: {str(e)}"))
            else:
                self.logger.info("未配置API密码或installer，跳过自动安装")
            
            return result
            
        except Exception as e:
            self.logger.error(f"插件生成流程失败：{str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            self.generation_status["is_generating"] = False
            self.generation_status["current_step"] = 0
            self.generation_status["progress_percentage"] = 0
            self.generation_status["plugin_name"] = ""
        
    async def _review_code_with_retry(self, code: str, metadata: Dict[str, Any],
                                    markdown: str,
                                    max_retries: int = 3) -> Dict[str, Any]:
        """带重试的代码审查
        
        Args:
            code: 插件代码
            metadata: 插件元数据
            markdown: 插件Markdown文档
            max_retries: 最大重试次数
            
        Returns:
            Dict[str, Any]: 审查结果
        """
        for attempt in range(max_retries):
            try:
                return await self.llm_handler.review_plugin_code(code, metadata, markdown)
            except Exception as e:
                self.logger.error(f"代码审查失败（尝试 {attempt + 1}/{max_retries}）：{str(e)}")
                if attempt == max_retries - 1:
                    # 返回一个默认的失败结果
                    return {
                        "approved": False,
                        "satisfaction_score": 0,
                        "reason": f"代码审查失败：{str(e)}",
                        "issues": ["代码审查失败"],
                        "suggestions": ["请检查代码并重试"]
                    }
                    
        return {
            "approved": False,
            "satisfaction_score": 0,
            "reason": "代码审查失败",
            "issues": ["代码审查失败"],
            "suggestions": ["请检查代码并重试"]
        }
        
    async def _create_plugin_files(self, plugin_name: str, metadata: Dict[str, Any], code: str, markdown: str) -> str:
        """创建插件文件
        
        Args:
            plugin_name: 插件名称
            metadata: 插件元数据
            code: 插件代码
            markdown: Markdown文档
            
        Returns:
            str: 插件路径
        """
        # 获取插件目录
        plugins_dir = self.directory_detector.get_plugins_directory()
        if not plugins_dir:
            raise ValueError("无法获取插件目录")
            
        # 创建插件目录
        plugin_dir = create_plugin_directory(plugins_dir, plugin_name)
        
        # 创建main.py
        main_py_path = os.path.join(plugin_dir, "main.py")
        with open(main_py_path, 'w', encoding='utf-8') as f:
            f.write(code)
            
        # 创建metadata.yaml
        metadata_yaml_path = os.path.join(plugin_dir, "metadata.yaml")
        metadata_content = metadata.get('metadata', {}) if isinstance(metadata.get('metadata'), dict) else {}
        with open(metadata_yaml_path, 'w', encoding='utf-8') as f:
            f.write(f"""name: {metadata.get('name', plugin_name)}
author: {metadata.get('author', 'CodeMage')}
description: {metadata.get('description', '由CodeMage生成的插件')}
version: {metadata.get('version', '1.0.0')}
repo: {metadata_content.get('repo_url', '')}
""")
            
        # 创建requirements.txt（如果有依赖）
        dependencies = metadata_content.get('dependencies', []) if isinstance(metadata_content, dict) else []
        if dependencies and self.config.get('allow_dependencies', True):
            requirements_path = os.path.join(plugin_dir, "requirements.txt")
            with open(requirements_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(dependencies))
            self.logger.info(f"已创建requirements.txt文件，包含{len(dependencies)}个依赖")
        else:
            self.logger.info("未创建requirements.txt文件（无依赖或依赖生成被禁用）")
                
        # 创建README.md
        readme_path = os.path.join(plugin_dir, "README.md")
        readme_content = markdown if markdown.strip() else f"# {metadata.get('name', plugin_name)}\n\n由CodeMage生成的插件"
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)
            
        return plugin_dir