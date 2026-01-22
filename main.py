"""
AstrBot 插件上传安装器
支持通过文件上传或 URL 安装插件到 AstrBot
支持检索本地 plugins 目录并选择上传
"""

import os
import json
import hashlib
from typing import Dict, Any, Optional
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig
from astrbot.core.utils.session_waiter import session_waiter, SessionController
import astrbot.api.message_components as Comp

from .installer import PluginInstaller


@register(
    "astrbot_plugin_upload",
    "ikirito",
    "AstrBot 插件上传安装器，支持检索本地插件并上传安装",
    "1.1.0",
    "https://github.com/ikirito/astrbot_plugin_upload",
)
class PluginUploadPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.logger = logger

        # === 数据持久化配置 ===
        # 设定数据目录: data/astrbot_plugin_upload/
        # 这样更新插件本身时，数据目录不会被删除
        self.data_root = os.path.join(os.getcwd(), "data", "astrbot_plugin_upload")
        if not os.path.exists(self.data_root):
            try:
                os.makedirs(self.data_root, exist_ok=True)
            except Exception as e:
                self.logger.error(f"创建数据目录失败: {e}")

        # 1. 待上传插件仓库目录: data/astrbot_plugin_upload/repo/
        self.plugins_path = os.path.join(self.data_root, "repo")
        if not os.path.exists(self.plugins_path):
            os.makedirs(self.plugins_path, exist_ok=True)

        # 检查旧位置的 plugins 目录，如果有文件提示用户
        old_plugin_dir = os.path.dirname(os.path.abspath(__file__))
        old_plugins_path = os.path.join(old_plugin_dir, "plugins")
        if os.path.exists(old_plugins_path) and os.listdir(old_plugins_path):
            # 仅记录日志，不自动移动文件，防止误操作
            self.logger.info(f"提示：检测到旧插件目录 {old_plugins_path} 中有文件，建议手动移动到 {self.plugins_path}")

        # 初始化安装器
        self._init_installer()

    def _init_installer(self):
        """初始化安装器，自动处理密码 MD5"""
        # 从配置中读取信息
        astrbot_url = self.config.get("astrbot_url", "http://localhost:6185")
        api_username = self.config.get("api_username", "astrbot")
        api_password = self.config.get("api_password", "")

        # 计算密码 MD5
        api_password_md5 = ""
        if api_password:
            api_password_md5 = self._md5(api_password)

        # 构建配置供 installer 使用
        installer_config = dict(self.config) if hasattr(self.config, '__iter__') else {}
        installer_config["astrbot_url"] = astrbot_url
        installer_config["api_username"] = api_username
        installer_config["api_password_md5"] = api_password_md5

        self.installer = PluginInstaller(installer_config)

    def _is_configured(self) -> bool:
        """检查是否已配置凭据"""
        return bool(self.config.get("api_password"))

    def _get_available_plugins(self) -> list:
        """获取 plugins 目录下的可用插件列表"""
        plugins = []

        if not os.path.exists(self.plugins_path):
            return plugins

        for item in os.listdir(self.plugins_path):
            item_path = os.path.join(self.plugins_path, item)
            if os.path.isdir(item_path) and not item.startswith('.'):
                # 检查是否包含 main.py 或 metadata.yaml（标准插件结构）
                has_main = os.path.exists(os.path.join(item_path, 'main.py'))
                has_metadata = os.path.exists(os.path.join(item_path, 'metadata.yaml'))

                if has_main or has_metadata:
                    # 尝试读取插件描述
                    desc = ""
                    if has_metadata:
                        try:
                            import yaml
                            with open(os.path.join(item_path, 'metadata.yaml'), 'r', encoding='utf-8') as f:
                                meta = yaml.safe_load(f)
                                desc = meta.get('desc', '')
                        except:
                            pass

                    plugins.append({
                        "name": item,
                        "path": item_path,
                        "desc": desc
                    })

        return plugins

    def _md5(self, text: str) -> str:
        """计算 MD5 值"""
        return hashlib.md5(text.encode()).hexdigest()

    def _check_admin_permission(self, event: AstrMessageEvent) -> bool:
        """检查管理员权限"""
        if not self.config.get("admin_only", True):
            return True

        try:
            if hasattr(event, "is_admin"):
                is_admin_attr = getattr(event, "is_admin")
                if callable(is_admin_attr):
                    if is_admin_attr():
                        return True
                else:
                    if bool(is_admin_attr):
                        return True

            role = getattr(event, "role", None)
            if isinstance(role, str) and role.lower() == "admin":
                return True
        except Exception as e:
            self.logger.warning(f"检查管理员权限时发生错误: {str(e)}")

        # 兼容性兜底：从 AstrBot 配置里匹配管理员 ID 列表
        try:
            sender_id = str(event.get_sender_id())
            astrbot_config = self.context.get_config()
            for key in ("admins", "admin_ids", "admin_list", "superusers", "super_users"):
                ids = astrbot_config.get(key, [])
                if isinstance(ids, (list, tuple, set)):
                    if sender_id in {str(i) for i in ids}:
                        return True
        except Exception:
            pass

        return False

    @filter.command("上传插件", alias={"upload_plugin", "install_plugin"})
    async def upload_plugin_command(self, event: AstrMessageEvent):
        """上传并安装插件指令

        用法：发送 /上传插件 命令后，附带 ZIP 文件
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("仅管理员可以使用此功能")
            return

        # 检查是否有附件
        files = []
        try:
            # 尝试获取消息中的文件附件
            if hasattr(event, 'message') and hasattr(event.message, 'message'):
                for seg in event.message.message:
                    if hasattr(seg, 'type') and seg.type == 'file':
                        if hasattr(seg, 'file'):
                            files.append(seg.file)
                        elif hasattr(seg, 'data') and 'file' in seg.data:
                            files.append(seg.data['file'])
        except Exception as e:
            self.logger.error(f"获取文件附件失败: {e}")

        if not files:
            yield event.plain_result(
                "请发送插件 ZIP 文件\n"
                "用法：发送 /上传插件 命令并附带 ZIP 文件\n"
                "或使用 /安装插件 <插件目录路径> 从本地安装"
            )
            return

        # 处理第一个文件
        file_path = files[0]
        if not file_path.endswith('.zip'):
            yield event.plain_result("请上传 ZIP 格式的插件文件")
            return

        yield event.plain_result("正在安装插件...")

        try:
            result = await self.installer.install_plugin(file_path)

            if result.get("success"):
                plugin_name = result.get("plugin_name", "未知")
                yield event.plain_result(f"插件安装成功！\n插件名称：{plugin_name}")
            else:
                error = result.get("error", "未知错误")
                yield event.plain_result(f"插件安装失败：{error}")
        except Exception as e:
            self.logger.error(f"插件安装过程中发生错误: {str(e)}")
            yield event.plain_result(f"插件安装失败：{str(e)}")

    @filter.command("安装插件", alias={"install_local"})
    async def install_local_plugin(self, event: AstrMessageEvent, plugin_path: str = ""):
        """从本地路径安装插件

        Args:
            plugin_path: 插件目录路径
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("仅管理员可以使用此功能")
            return

        if not plugin_path:
            yield event.plain_result("请提供插件目录路径，例如：/安装插件 /path/to/plugin")
            return

        if not os.path.exists(plugin_path):
            yield event.plain_result(f"路径不存在：{plugin_path}")
            return

        if not os.path.isdir(plugin_path):
            yield event.plain_result("请提供插件目录路径，而非文件路径")
            return

        yield event.plain_result("正在打包并安装插件...")

        try:
            # 打包插件
            zip_path = await self.installer.create_plugin_zip(plugin_path)
            if not zip_path:
                yield event.plain_result("插件打包失败")
                return

            # 安装插件
            plugin_name = os.path.basename(os.path.normpath(plugin_path))
            result = await self.installer.install_plugin(zip_path, plugin_name)

            # 清理临时文件
            try:
                os.remove(zip_path)
            except Exception:
                pass

            if result.get("success"):
                yield event.plain_result(f"插件安装成功！\n插件名称：{result.get('plugin_name', plugin_name)}")
            else:
                error = result.get("error", "未知错误")
                yield event.plain_result(f"插件安装失败：{error}")
        except Exception as e:
            self.logger.error(f"插件安装过程中发生错误: {str(e)}")
            yield event.plain_result(f"插件安装失败：{str(e)}")

    @filter.command("卸载插件", alias={"uninstall_plugin", "remove_plugin"})
    async def uninstall_plugin_command(self, event: AstrMessageEvent, plugin_name: str = ""):
        """卸载已安装的插件

        Args:
            plugin_name: 插件名称
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            yield event.plain_result("仅管理员可以使用此功能")
            return

        if not plugin_name:
            yield event.plain_result("请提供要卸载的插件名称，例如：/卸载插件 my_plugin")
            return

        yield event.plain_result(f"正在卸载插件：{plugin_name}...")

        try:
            result = await self.installer.delete_plugin_folder(plugin_name)

            if result.get("success"):
                yield event.plain_result(f"插件卸载成功：{plugin_name}")
            else:
                error = result.get("error", "未知错误")
                yield event.plain_result(f"插件卸载失败：{error}")
        except Exception as e:
            self.logger.error(f"插件卸载过程中发生错误: {str(e)}")
            yield event.plain_result(f"插件卸载失败：{str(e)}")

    @filter.command("插件列表", alias={"list_plugins", "plugins"})
    async def list_plugins_command(self, event: AstrMessageEvent):
        """列出本地可用的插件"""
        # 检查管理员权限
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        plugins = self._get_available_plugins()

        if not plugins:
            await event.send(event.plain_result(
                f"未找到可用插件\n"
                f"插件目录：{self.plugins_path}\n"
                f"请将插件文件夹放入该目录"
            ))
            return

        result_lines = ["📦 本地可用插件列表：\n"]
        for i, plugin in enumerate(plugins, 1):
            desc = f" - {plugin['desc']}" if plugin['desc'] else ""
            result_lines.append(f"{i}. {plugin['name']}{desc}")

        result_lines.append(f"\n请直接回复序号进行安装（回复 0 取消）")

        message_result = event.plain_result("\n".join(result_lines))
        await event.send(message_result)

        # 进入等待模式，复用选择逻辑
        @session_waiter(timeout=60, record_history_chains=False)
        async def plugin_selection_waiter(controller: SessionController, event: AstrMessageEvent):
            try:
                user_input = event.message_str.strip()

                if user_input == "0" or user_input.lower() == "q":
                    message_result = event.make_result()
                    message_result.chain = [Comp.Plain("操作已取消")]
                    await event.send(message_result)
                    controller.stop()
                    return

                try:
                    idx = int(user_input) - 1
                    if 0 <= idx < len(plugins):
                        selected = plugins[idx]
                        await self._do_install_plugin(event, selected, controller)
                    else:
                        message_result = event.make_result()
                        message_result.chain = [Comp.Plain("无效的序号，请重新输入（输入 0 取消）")]
                        await event.send(message_result)
                        controller.keep(timeout=60, reset_timeout=True)
                except ValueError:
                    # 如果输入的不是数字，可能是其他指令，停止等待以免干扰
                    # 或者提示输入数字。为了体验，这里选择忽略非数字输入或提示
                    # 考虑到用户可能想执行其他命令，如果不是数字，我们可以停止等待
                    # 但为了防止误操作，还是提示一下比较好，或者静默退出？
                    # 按照惯例，提示输入数字
                    message_result = event.make_result()
                    message_result.chain = [Comp.Plain("请输入有效的数字序号")]
                    await event.send(message_result)
                    controller.keep(timeout=60, reset_timeout=True)
            except Exception as e:
                self.logger.error(f"选择插件时出错: {e}")
                message_result = event.make_result()
                message_result.chain = [Comp.Plain(f"发生错误: {str(e)}")]
                await event.send(message_result)
                controller.stop()

        try:
            await plugin_selection_waiter(event)
        except Exception as e:
            self.logger.error(f"插件列表交互错误: {e}")
            await event.send(event.plain_result(f"发生错误：{str(e)}"))
        finally:
            event.stop_event()

    @filter.command("选择插件", alias={"select_plugin", "sp"})
    async def select_plugin_command(self, event: AstrMessageEvent, index: str = ""):
        """选择并安装本地插件

        Args:
            index: 插件序号
        """
        # 检查管理员权限
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        plugins = self._get_available_plugins()

        if not plugins:
            await event.send(event.plain_result("未找到可用插件，请先使用 /插件列表 查看"))
            return

        if not index:
            # 显示插件列表供选择
            result_lines = ["请选择要安装的插件（回复序号）：\n"]
            for i, plugin in enumerate(plugins, 1):
                desc = f" - {plugin['desc']}" if plugin['desc'] else ""
                result_lines.append(f"{i}. {plugin['name']}{desc}")

            message_result = event.plain_result("\n".join(result_lines))
            await event.send(message_result)

            # 使用会话等待用户选择
            @session_waiter(timeout=60, record_history_chains=False)
            async def plugin_selection_waiter(controller: SessionController, event: AstrMessageEvent):
                try:
                    user_input = event.message_str.strip()

                    if user_input == "0" or user_input.lower() == "q":
                        message_result = event.make_result()
                        message_result.chain = [Comp.Plain("操作已取消")]
                        await event.send(message_result)
                        controller.stop()
                        return

                    try:
                        idx = int(user_input) - 1
                        if 0 <= idx < len(plugins):
                            selected = plugins[idx]
                            await self._do_install_plugin(event, selected, controller)
                        else:
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("无效的序号，请重新输入（输入 0 取消）")]
                            await event.send(message_result)
                            controller.keep(timeout=60, reset_timeout=True)
                    except ValueError:
                        message_result = event.make_result()
                        message_result.chain = [Comp.Plain("请输入有效的数字序号")]
                        await event.send(message_result)
                        controller.keep(timeout=60, reset_timeout=True)
                except Exception as e:
                    self.logger.error(f"选择插件时出错: {e}")
                    message_result = event.make_result()
                    message_result.chain = [Comp.Plain(f"发生错误: {str(e)}")]
                    await event.send(message_result)
                    controller.stop()

            try:
                await plugin_selection_waiter(event)
            except Exception as e:
                self.logger.error(f"插件选择错误: {e}")
                await event.send(event.plain_result(f"发生错误：{str(e)}"))
            finally:
                event.stop_event()
        else:
            # 直接安装指定序号的插件
            try:
                idx = int(index) - 1
                if 0 <= idx < len(plugins):
                    selected = plugins[idx]
                    await self._do_install_plugin_direct(event, selected)
                else:
                    await event.send(event.plain_result(f"无效的序号：{index}"))
            except ValueError:
                await event.send(event.plain_result("请输入有效的数字序号"))

    async def _do_install_plugin(self, event: AstrMessageEvent, plugin: dict, controller: SessionController):
        """执行插件安装（会话模式）"""
        # 检查是否已配置凭据
        if not self._is_configured():
            message_result = event.make_result()
            message_result.chain = [Comp.Plain("尚未配置 AstrBot 凭据\n请先使用 /配置凭据 命令进行配置")]
            await event.send(message_result)
            controller.stop()
            return

        message_result = event.make_result()
        message_result.chain = [Comp.Plain(f"正在安装插件：{plugin['name']}...")]
        await event.send(message_result)

        try:
            # 打包并安装
            zip_path = await self.installer.create_plugin_zip(plugin['path'])
            if not zip_path:
                message_result = event.make_result()
                message_result.chain = [Comp.Plain("插件打包失败")]
                await event.send(message_result)
                controller.stop()
                return

            result = await self.installer.install_plugin(zip_path, plugin['name'])

            # 清理临时文件
            try:
                os.remove(zip_path)
            except:
                pass

            message_result = event.make_result()
            if result.get("success"):
                message_result.chain = [Comp.Plain(f"✅ 插件安装成功！\n插件名称：{result.get('plugin_name', plugin['name'])}")]
            else:
                message_result.chain = [Comp.Plain(f"❌ 插件安装失败：{result.get('error', '未知错误')}")]
            await event.send(message_result)

        except Exception as e:
            self.logger.error(f"安装插件时出错: {e}")
            message_result = event.make_result()
            message_result.chain = [Comp.Plain(f"安装失败：{str(e)}")]
            await event.send(message_result)

        controller.stop()

    async def _do_install_plugin_direct(self, event: AstrMessageEvent, plugin: dict):
        """执行插件安装（直接模式）"""
        # 检查是否已配置凭据
        if not self._is_configured():
            await event.send(event.plain_result("尚未配置 AstrBot 凭据\n请先使用 /配置凭据 命令进行配置"))
            return

        await event.send(event.plain_result(f"正在安装插件：{plugin['name']}..."))

        try:
            zip_path = await self.installer.create_plugin_zip(plugin['path'])
            if not zip_path:
                await event.send(event.plain_result("插件打包失败"))
                return

            result = await self.installer.install_plugin(zip_path, plugin['name'])

            try:
                os.remove(zip_path)
            except:
                pass

            if result.get("success"):
                await event.send(event.plain_result(f"✅ 插件安装成功！\n插件名称：{result.get('plugin_name', plugin['name'])}"))
            else:
                await event.send(event.plain_result(f"❌ 插件安装失败：{result.get('error', '未知错误')}"))

        except Exception as e:
            self.logger.error(f"安装插件时出错: {e}")
            await event.send(event.plain_result(f"安装失败：{str(e)}"))

    @filter.command("插件帮助", alias={"plugin_help"})
    async def show_help(self, event: AstrMessageEvent):
        """显示插件帮助信息"""
        help_text = """📖 插件上传安装器帮助

【插件管理】
  /插件列表       - 查看本地可用插件
  /选择插件 [序号] - 选择并安装插件
  /卸载插件 <名称> - 卸载已安装的插件

【其他方式】
  /上传插件       - 上传 ZIP 文件安装
  /安装插件 <路径> - 从指定路径安装
  /插件帮助       - 显示此帮助

【使用流程】
1. 在插件配置中填写 API 密码
2. 使用 /插件列表 查看可用插件
3. 使用 /选择插件 序号 进行安装

【注意事项】
- 仅管理员可以使用此功能
- 默认地址：localhost:6185"""
        await event.send(event.plain_result(help_text))

    async def terminate(self):
        """插件卸载时调用"""
        self.logger.info("插件上传安装器已卸载")
