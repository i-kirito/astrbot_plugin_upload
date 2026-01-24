"""
AstrBot 插件上传安装器
支持通过文件上传或 URL 安装插件到 AstrBot
支持检索本地 plugins 目录并选择上传
"""

import os
import json
import hashlib
import asyncio
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
    "1.3.2",
    "https://github.com/ikirito/astrbot_plugin_upload",
)
class PluginUploadPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.logger = logger

        # === 数据持久化配置 ===
        # 设定数据目录: data/astrbot_plugin_upload/
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
            self.logger.info(f"提示：检测到旧插件目录 {old_plugins_path} 中有文件，建议手动移动到 {self.plugins_path}")

        # 初始化安装器
        self._init_installer()

    def _init_installer(self):
        """初始化安装器，自动处理密码 MD5"""
        astrbot_url = self.config.get("astrbot_url", "http://localhost:6185")
        api_username = self.config.get("api_username", "astrbot")
        api_password = self.config.get("api_password", "")

        api_password_md5 = ""
        if api_password:
            api_password_md5 = self._md5(api_password)

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
                has_main = os.path.exists(os.path.join(item_path, 'main.py'))
                has_metadata = os.path.exists(os.path.join(item_path, 'metadata.yaml'))

                if has_main or has_metadata:
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
        return hashlib.md5(text.encode()).hexdigest()

    def _check_admin_permission(self, event: AstrMessageEvent) -> bool:
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

    @filter.command("插件市场", alias={"plugin_market", "market"})
    async def market_command(self, event: AstrMessageEvent, index: str = ""):
        """浏览并安装 i-kirito 的 AstrBot 插件"""
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        # 获取远程插件列表
        await event.send(event.plain_result("🌐 正在获取插件市场列表..."))

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.github.com/users/i-kirito/repos") as resp:
                    if resp.status != 200:
                        await event.send(event.plain_result(f"❌ 获取失败: HTTP {resp.status}"))
                        return
                    repos = await resp.json()
        except Exception as e:
            await event.send(event.plain_result(f"❌ 网络请求失败: {e}"))
            return

        # 筛选插件
        market_plugins = []
        for repo in repos:
            if isinstance(repo, dict) and repo.get("name", "").startswith("astrbot_plugin_"):
                market_plugins.append({
                    "name": repo["name"],
                    "url": repo["html_url"],
                    "desc": repo.get("description", "无描述")
                })

        if not market_plugins:
            await event.send(event.plain_result("📭 未发现任何 AstrBot 插件仓库"))
            return

        # 如果直接带了参数
        if index:
            try:
                idx = int(index) - 1
                if 0 <= idx < len(market_plugins):
                    selected = market_plugins[idx]
                    await event.send(event.plain_result(f"🚀 正在从市场安装: {selected['name']}"))

                    # 复用 URL 安装逻辑
                    result = await self.installer.install_from_url(selected['url'])
                    await self._send_install_result(event, result)
                    return
                else:
                    await event.send(event.plain_result(f"❌ 无效的序号：{index}"))
                    return
            except ValueError:
                pass

        # 显示列表
        result_lines = ["🏪 i-kirito 插件市场：\n"]
        for i, plugin in enumerate(market_plugins, 1):
            desc = f" - {plugin['desc']}" if plugin['desc'] else ""
            result_lines.append(f"{i}. {plugin['name']}{desc}")

        result_lines.append(f"\n请直接回复序号进行安装（回复 0 取消）")

        await event.send(event.plain_result("\n".join(result_lines)))

        # 进入等待模式
        @session_waiter(timeout=60, record_history_chains=False)
        async def market_selection_waiter(controller: SessionController, event: AstrMessageEvent):
            try:
                user_input = event.message_str.strip()
                if user_input == "0" or user_input.lower() == "q":
                    await event.send(event.plain_result("操作已取消"))
                    controller.stop()
                    return

                try:
                    idx = int(user_input) - 1
                    if 0 <= idx < len(market_plugins):
                        selected = market_plugins[idx]
                        await event.send(event.plain_result(f"🚀 正在安装: {selected['name']}..."))

                        # URL 安装
                        install_res = await self.installer.install_from_url(selected['url'])
                        await self._send_install_result(event, install_res)
                        controller.stop()
                    else:
                        await event.send(event.plain_result("❌ 无效序号，请重试"))
                        controller.keep(timeout=60, reset_timeout=True)
                except ValueError:
                    await event.send(event.plain_result("❌ 请输入数字序号"))
                    controller.keep(timeout=60, reset_timeout=True)
            except Exception as e:
                self.logger.error(f"市场交互错误: {e}")
                controller.stop()

        try:
            await market_selection_waiter(event)
        except Exception as e:
            self.logger.error(f"市场会话错误: {e}")
        finally:
            event.stop_event()

    @filter.command("插件安装", alias={"install_plugin", "plugin_install"})
    async def install_plugin_command(self, event: AstrMessageEvent, arg: str = ""):
        """安装插件 (支持 ZIP/URL/本地路径)

        Args:
            arg: 可选参数，可以是 GitHub 链接或本地路径
        """
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        # 1. 检查附件 (ZIP)
        files = []
        try:
            if hasattr(event, 'message') and hasattr(event.message, 'message'):
                for seg in event.message.message:
                    if hasattr(seg, 'type') and seg.type == 'file':
                        if hasattr(seg, 'file'):
                            files.append(seg.file)
                        elif hasattr(seg, 'data') and 'file' in seg.data:
                            files.append(seg.data['file'])
        except Exception as e:
            self.logger.error(f"获取文件附件失败: {e}")

        if files:
            file_path = files[0]
            if not file_path.endswith('.zip'):
                await event.send(event.plain_result("请上传 ZIP 格式的插件文件"))
                return

            await event.send(event.plain_result("📦 收到 ZIP 文件，正在安装..."))
            result = await self.installer.install_plugin(file_path)
            await self._send_install_result(event, result)
            return

        # 2. 检查参数
        if not arg:
            await event.send(event.plain_result(
                "请提供插件来源：\n"
                "1. 发送 ZIP 文件的同时输入指令\n"
                "2. 输入 GitHub 仓库链接\n"
                "3. 输入本地插件目录路径"
            ))
            return

        if arg.startswith("http"):
            # URL 安装
            await event.send(event.plain_result(f"🌐 正在从 URL 下载并安装: {arg}"))
            result = await self.installer.install_from_url(arg)
            await self._send_install_result(event, result)
        else:
            # 不支持的输入
            await event.send(event.plain_result("❌ 请输入有效的 GitHub 链接或直接发送 ZIP 文件"))

    @filter.command("插件更新", alias={"update_plugin", "plugin_update"})
    async def update_plugin_command(self, event: AstrMessageEvent, plugin_name: str = ""):
        """更新插件 (针对本地 Repo 中的插件)
        不带参数则更新所有插件
        """
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        if plugin_name:
            # 更新指定插件
            await self._update_single_plugin_logic(event, plugin_name)
        else:
            # 批量更新所有插件
            plugins = self._get_available_plugins()
            if not plugins:
                await event.send(event.plain_result("本地仓库中没有可更新的插件"))
                return

            await event.send(event.plain_result(f"🔄 开始批量更新 {len(plugins)} 个插件..."))

            success_list = []
            fail_list = []

            for plugin in plugins:
                name = plugin['name']
                path = plugin['path']

                try:
                    result = await self._perform_plugin_update(name, path)
                    if result.get("success"):
                        success_list.append(name)
                    else:
                        fail_list.append(f"{name} ({result.get('error')})")
                except Exception as e:
                    fail_list.append(f"{name} ({str(e)})")

            # 汇总报告
            msg = f"📊 批量更新完成\n"
            if success_list:
                msg += f"✅ 成功 ({len(success_list)}): {', '.join(success_list)}\n"
            if fail_list:
                msg += f"❌ 失败 ({len(fail_list)}): {', '.join(fail_list)}"

            await event.send(event.plain_result(msg.strip()))

    async def _update_single_plugin_logic(self, event: AstrMessageEvent, plugin_name: str):
        """处理单个插件更新的指令逻辑"""
        # 检查 repo 中是否存在该插件
        repo_plugin_path = os.path.join(self.plugins_path, plugin_name)
        if not os.path.exists(repo_plugin_path):
            # 尝试模糊匹配
            candidates = [p for p in os.listdir(self.plugins_path) if plugin_name in p and os.path.isdir(os.path.join(self.plugins_path, p))]
            if len(candidates) == 1:
                plugin_name = candidates[0]
                repo_plugin_path = os.path.join(self.plugins_path, plugin_name)
            else:
                await event.send(event.plain_result(f"❌ 在本地仓库中未找到插件: {plugin_name}"))
                return

        await event.send(event.plain_result(f"🔄 正在更新插件: {plugin_name}"))

        result = await self._perform_plugin_update(plugin_name, repo_plugin_path)
        await self._send_install_result(event, result)

    async def _perform_plugin_update(self, plugin_name: str, repo_path: str) -> dict:
        """执行插件更新的核心逻辑 (Git Pull + Reinstall)"""
        # 1. 如果是 Git 仓库，尝试 git pull
        if os.path.exists(os.path.join(repo_path, ".git")):
            try:
                process = await asyncio.create_subprocess_exec(
                    "git", "pull",
                    cwd=repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await process.communicate()
                # 这里我们不根据 git 结果中断，因为即使 git 失败，可能用户只是想重新安装
            except Exception as e:
                self.logger.error(f"Git 更新出错: {e}")

        # 2. 重新打包安装
        zip_path = await self.installer.create_plugin_zip(repo_path)
        if not zip_path:
            return {"success": False, "error": "打包失败"}

        result = await self.installer.install_plugin(zip_path, plugin_name)
        try:
            os.remove(zip_path)
        except:
            pass

        return result

    @filter.command("插件列表", alias={"list_plugins", "plugins"})
    async def list_plugins_command(self, event: AstrMessageEvent, index: str = ""):
        """列出本地可用的插件"""
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

        # 如果直接带了参数 (例如 /插件列表 1)
        if index:
            try:
                idx = int(index) - 1
                if 0 <= idx < len(plugins):
                    selected = plugins[idx]
                    await event.send(event.plain_result(f"🚀 直接安装第 {index} 号插件: {selected['name']}"))
                    await self._do_install_plugin_direct(event, selected)
                    return
                else:
                    await event.send(event.plain_result(f"❌ 无效的序号：{index}"))
                    return
            except ValueError:
                pass

        result_lines = ["📦 本地可用插件列表：\n"]
        for i, plugin in enumerate(plugins, 1):
            desc = f" - {plugin['desc']}" if plugin['desc'] else ""
            result_lines.append(f"{i}. {plugin['name']}{desc}")

        result_lines.append(f"\n请直接回复序号进行安装（回复 0 取消）")

        message_result = event.plain_result("\n".join(result_lines))
        await event.send(message_result)

        # 进入等待模式
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
            self.logger.error(f"插件列表交互错误: {e}")
            await event.send(event.plain_result(f"发生错误：{str(e)}"))
        finally:
            event.stop_event()

    @filter.command("卸载插件", alias={"uninstall_plugin", "remove_plugin"})
    async def uninstall_plugin_command(self, event: AstrMessageEvent, plugin_name: str = ""):
        """卸载已安装的插件"""
        if not self._check_admin_permission(event):
            await event.send(event.plain_result("仅管理员可以使用此功能"))
            return

        if not plugin_name:
            await event.send(event.plain_result("请提供要卸载的插件名称，例如：/卸载插件 my_plugin"))
            return

        await event.send(event.plain_result(f"正在卸载插件：{plugin_name}..."))

        try:
            result = await self.installer.delete_plugin_folder(plugin_name)

            if result.get("success"):
                await event.send(event.plain_result(f"插件卸载成功：{plugin_name}"))
            else:
                error = result.get("error", "未知错误")
                await event.send(event.plain_result(f"插件卸载失败：{error}"))
        except Exception as e:
            self.logger.error(f"插件卸载过程中发生错误: {str(e)}")
            await event.send(event.plain_result(f"插件卸载失败：{str(e)}"))

    @filter.command("插件帮助", alias={"plugin_help"})
    async def show_help(self, event: AstrMessageEvent):
        """显示插件帮助信息"""
        help_text = """📖 AstrBot 插件上传安装器帮助

💻 指令列表：
  • /插件安装 [URL/ZIP/路径]
    - 智能安装指令，支持多种来源。
    - 示例：/插件安装 https://github.com/user/repo

  • /插件市场 [序号]
    - 浏览 i-kirito 官方插件市场。
    - 回复序号即可一键安装。

  • /插件列表 [序号]
    - 查看本地 repo 目录下的插件。
    - 回复序号即可安装。

  • /插件更新 <名称>
    - 更新指定插件 (支持 Git 仓库自动 Pull)。

  • /卸载插件 <名称>
    - 卸载已安装的插件。

💡 提示：
  - 仅管理员可用。
  - 插件库位置：data/astrbot_plugin_upload/repo/"""
        await event.send(event.plain_result(help_text))

    async def _do_install_plugin(self, event: AstrMessageEvent, plugin: dict, controller: SessionController):
        """执行插件安装（会话模式）"""
        if not self._is_configured():
            message_result = event.make_result()
            message_result.chain = [Comp.Plain("API 密码未配置，请先在后台插件配置中填写 api_password")]
            await event.send(message_result)
            controller.stop()
            return

        message_result = event.make_result()
        message_result.chain = [Comp.Plain(f"正在安装插件：{plugin['name']}...")]
        await event.send(message_result)

        await self._install_logic(event, plugin['path'], plugin['name'])
        controller.stop()

    async def _do_install_plugin_direct(self, event: AstrMessageEvent, plugin: dict):
        """执行插件安装（直接模式）"""
        if not self._is_configured():
            await event.send(event.plain_result("API 密码未配置，请先在后台插件配置中填写 api_password"))
            return

        await event.send(event.plain_result(f"正在安装插件：{plugin['name']}..."))
        await self._install_logic(event, plugin['path'], plugin['name'])

    async def _install_logic(self, event: AstrMessageEvent, path: str, name: str):
        """安装逻辑核心"""
        try:
            zip_path = await self.installer.create_plugin_zip(path)
            if not zip_path:
                await self._send_install_result(event, {"success": False, "error": "插件打包失败"})
                return

            result = await self.installer.install_plugin(zip_path, name)
            try:
                os.remove(zip_path)
            except:
                pass
            await self._send_install_result(event, result)
        except Exception as e:
            self.logger.error(f"安装插件时出错: {e}")
            await self._send_install_result(event, {"success": False, "error": str(e)})

    async def _send_install_result(self, event: AstrMessageEvent, result: dict):
        """发送安装结果辅助方法"""
        if result.get("success"):
            await event.send(event.plain_result(f"✅ 插件安装成功！\n插件名称：{result.get('plugin_name', '未知')}"))
        else:
            await event.send(event.plain_result(f"❌ 插件安装失败：{result.get('error', '未知错误')}"))

    async def terminate(self):
        """插件卸载时调用"""
        self.logger.info("插件上传安装器已卸载")
