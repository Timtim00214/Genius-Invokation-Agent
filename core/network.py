import sys
import os
import asyncio
import json
import httpx
from httpx_sse import aconnect_sse
import logging
import webbrowser # 用于自动打开浏览器
from colorama import Fore, Style, init

# ==========================================
# 🛠️ 环境与路径配置
# ==========================================
# 获取当前文件所在路径 (core/)
current_file_path = os.path.abspath(__file__)
# 获取项目根路径 (Genshin_Agent/)
project_root = os.path.dirname(os.path.dirname(current_file_path))
# 定位 Proto 编译文件夹
proto_dir = os.path.join(project_root, "proto_compiled")

# 将路径加入 sys.path 以便导入
sys.path.insert(0, proto_dir)
sys.path.insert(1, project_root)

# 初始化彩色输出
init(autoreset=True)

# ==========================================
# 🧬 协议加载 (Proto)
# ==========================================
try:
    # 尝试导入编译好的 Proto 文件
    # 注意：根据实际生成的文件名可能需要调整 (例如 server_notification_pb2)
    import notification_pb2
    import state_pb2
    from google.protobuf.json_format import MessageToDict
    print(Fore.GREEN + "✅ Proto 协议库加载成功")
except ImportError as e:
    print(Fore.RED + f"⚠️ Proto 加载警告: {e}")
    print(Fore.YELLOW + "   (将在无 Proto 解析模式下运行，仅显示原始数据)")

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("AGCAgent")

# ==========================================
# 🃏 黄金卡组 (Golden Deck) - Ver 24
# ==========================================
# 来源：直接抓取自前端能够成功创建房间的请求
SAMPLE_DECK = {
    "characters": [1112, 1213, 1101],
    # 注意：抓包显示字段为 'cards'，如果服务端报字段错误，可尝试改为 'actions'
    "cards": [
        311308, 311308, 312010, 312010, 321024, 321024, 322005, 322005, 
        322016, 322016, 322024, 322027, 322027, 330007, 331102, 332004, 
        332029, 332029, 332040, 332040, 332043, 332044, 332044, 332045, 
        332045, 332049, 333020, 333020, 333027, 333027
    ]
}

class GenshinTCGBot:
    def __init__(self, base_url="http://localhost:3000/api"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(base_url=base_url, timeout=None)
        self.token = None
        self.player_id = None
        self.room_id = None
        # [新增] 用于记忆最近的战场状态，以便查询 Entity ID
        self.latest_state = None
    def generate_debug_link(self):
        """
        生成一个 HTML 文件，双击打开后会自动写入 Token 并跳转到前端页面 (5173)。
        """
        if not self.room_id or not self.token:
            return

        # 这里使用你提供的客户端端口 5173
        frontend_url = f"http://localhost:5173/rooms/{self.room_id}"
        
        html_content = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI 视角接入中...</title>
    <style>
        body {{ font-family: sans-serif; background: #1a1a1a; color: #fff; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; }}
        .loader {{ border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <h1>🤖 正在接入 AI 视角...</h1>
    <div class="loader"></div>
    <p>Target: {frontend_url}</p>
    <p>Player: {self.player_id}</p>
    
    <script>
        // 1. 模拟 AI 的身份信息
        const token = "{self.token}";
        const playerId = "{self.player_id}";
        
        console.log("Injecting credentials...");
        
        // 2. 注入 LocalStorage (涵盖常见的键名)
        localStorage.setItem('accessToken', token); 
        localStorage.setItem('token', token);
        localStorage.setItem('playerId', playerId);
        
        // 3. 延迟跳转，确保存储写入完成
        setTimeout(() => {{
            window.location.href = "{frontend_url}";
        }}, 500);
    </script>
</body>
</html>
        """
        
        # 将文件写入项目根目录
        filename = "debug_ai_view.html"
        file_path = os.path.abspath(filename)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(Fore.CYAN + f"\n🐛 [调试神器] AI 视角入口已生成: {filename}")
            print(Fore.CYAN + f"👉 双击文件或访问: file:///{file_path.replace(os.sep, '/')}\n")
            
            # [新增] 自动在默认浏览器中打开
            webbrowser.open('file://' + file_path) 
            
        except Exception as e:
            print(Fore.RED + f"❌ 生成调试文件失败: {e}")

    async def login_guest(self, name="Agent_001", custom_config=None):
        print(Fore.YELLOW + f"🚀 正在发起连接... [Target: {self.base_url}]")
        
        # 1. 定义平铺的基础配置 (Flattened Config)
        # 根据 RoomDialog.tsx，这些必须直接放在根节点
        payload = {
            "name": name,
            "password": "",
            "gameVersion": 27,
            "isPvp": False,
            "botId": 0,
            
            # --- 官方时间参数 (直接平铺) ---
            "initTotalActionTime": 45,
            "rerollTime": 40,
            "roundTotalActionTime": 60,
            "actionTime": 25,

            # --- 权限参数 (直接平铺) ---
            "private": False,     # 对应 !isPublic()，设为 False 才能在大厅看到
            "watchable": True,    # 允许观战
            "allowGuest": True,   # 允许游客
            
            # --- 卡组 ---
            "deck": SAMPLE_DECK
        }

        # 2. 如果有自定义配置，直接更新到根节点
        if custom_config:
            # 注意：custom_config 里的键名必须也是 initTotalActionTime 这种
            payload.update(custom_config)

        try:
            # 发送请求
            resp = await self.client.post("/rooms", json=payload, timeout=10.0)
            
            if resp.status_code in [200, 201]:
                data = resp.json()
                
                # 提取关键凭证
                self.token = data.get("accessToken")
                self.player_id = data.get("playerId")
                # 兼容返回结构：有的版本直接返回 room 对象，有的嵌套
                room_info = data.get("room", {})
                self.room_id = room_info.get("id") if room_info else data.get("roomId")
                
                print(Fore.GREEN + f"✅ 房间创建成功!")
                print(Fore.CYAN + f"   🏠 Room ID: {self.room_id}")
                print(Fore.CYAN + f"   👤 Player ID: {self.player_id}")
                print(Fore.CYAN + f"   🔑 Token: {self.token}")
                
                # 生成调试网页
                self.generate_debug_link()

                return True
            
            else:
                # 失败处理：打印服务端返回的详细错误
                print(Fore.RED + f"❌ 创建房间失败 (Code {resp.status_code})")
                print(Fore.RED + f"   Server Says: {resp.text}")
                return False

        except httpx.ConnectError:
            print(Fore.RED + "❌ 连接被拒绝: 请确保 'npm run start' 或 'bun dev' 正在运行")
            return False
        except Exception as e:
            print(Fore.RED + f"💥 发生未知错误: {e}")
            return False

    async def listen_to_game(self):
        """ 监听 SSE 事件流 (Server-Sent Events) """
        if not self.token or not self.room_id:
            print(Fore.RED + "❌ 缺少 Token 或 RoomID，无法监听")
            return

        # SSE URL 拼接
        sse_path = f"/rooms/{self.room_id}/players/{self.player_id}/notification"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "text/event-stream"
        }


        print(Fore.YELLOW + f"📡 正在接入神经链路 (SSE)...")
        print(Fore.MAGENTA + f"   Endpoint: {sse_path}")

        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                # timeout=None 告诉 httpx：这条线是长连接，永远不要因为没数据而挂断
                async with aconnect_sse(self.client, "GET", sse_path, headers=headers, timeout=None) as event_source:
                    print(Fore.GREEN + "✅ 链路已建立，等待数据流...")
                    
                    async for sse in event_source.aiter_sse():
                        # 打印原始事件类型
                        print(Fore.BLUE + f"📩 [Event: {sse.event}] Size: {len(sse.data)} bytes")
                        
                        if sse.event == "message":
                            await self.handle_game_event(sse.data)
                        elif sse.event == "error":
                            print(Fore.RED + f"⚠️ Server Error Event: {sse.data}")
                            
            except httpx.ReadTimeout:
                print(Fore.YELLOW + "⚠️ 心跳超时，正在重连...")
                retry_count += 1
            except Exception as e:
                print(Fore.RED + f"❌ 监听中断: {e}")
                break
    async def send_action(self, payload):
        # 必须同时有 Token, RoomID 和 PlayerID 才能发送
        if not self.token or not self.room_id or not self.player_id:
            print(Fore.RED + "❌ 无法发送指令: 缺少必要连接信息")
            return False

        # ✅ 修正：使用你抓包得到的正确路径
        url = f"/rooms/{self.room_id}/players/{self.player_id}/actionResponse"

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        print(Fore.YELLOW + f"📤 正在发送指令 Payload: {json.dumps(payload, indent=None)}")

        try:
            # 发送响应
            resp = await self.client.post(url, json=payload, headers=headers, timeout=5.0)
            
            if resp.status_code == 200 or resp.status_code == 201:
                print(Fore.GREEN + f"✅ 指令发送成功!")
                return True
            else:
                print(Fore.RED + f"❌ 指令发送失败 ({resp.status_code}) URL: {resp.url}")
                print(Fore.RED + f"   Server Says: {resp.text}")
                return False
        except Exception as e:
            print(Fore.RED + f"💥 发送异常: {e}")
            return False
    async def handle_game_event(self, raw_data):
        """ 战术仪表盘：解析并清洗战场数据 """
        try:
            if not raw_data.startswith("{"):
                return

            event = json.loads(raw_data)
            evt_type = event.get("type")
            evt_data = event.get("data", {})

            # ==========================================
            # 1. 🔍 侦测游戏结束原因 (为何判负?)
            # ==========================================
            if evt_type == "gameEnd":
                winner = evt_data.get("winPlayerId")
                reason = evt_data.get("reason", "Unknown") # 获取判负原因
                print(Fore.RED + "="*50)
                print(Fore.RED + f"🏁 游戏结束! 获胜者: {winner}")
                print(Fore.RED + f"❓ 结束原因/判负理由: {reason}")
                print(Fore.RED + "="*50)
                return

            # ==========================================
            # 2. ⚡ 核心逻辑：响应 RPC 请求
            # ==========================================
            if evt_type == "rpc":
                rpc_id = evt_data.get("id")
                print(Fore.RED + f"⚡⚡⚡ [收到指令] Server 要求操作 | RPC ID: {rpc_id} ⚡⚡⚡")
                
                response_payload = None

                # --- RPC 0: 换牌 (Mulligan) ---
                if rpc_id == 0:
                    print(Fore.YELLOW + "🤖 [AI] 决定不换牌 (Keep All)")
                    response_payload = {
                        "id": rpc_id,
                        "response": {"switchHands": {"removedHandIds": []}}
                    }

                # --- RPC 1: 选首发 (Select Active) ---
                elif rpc_id == 1:
                    print(Fore.RED + "🤖 [AI] 正在计算最佳首发角色...")
                    
                    # 🎯 关键修复：从 State 中查找 Entity ID
                    target_def_id = 1112  # 我们想选的神里绫华/第一个角色
                    target_entity_id = None

                    if self.latest_state:
                        # 遍历我的角色列表，找到 definitionId 为 1112 的那个实体的 id
                        players = self.latest_state.get("player", [])
                        # 简单判定我是哪个 (假设我是 Guest/P1，或者根据 socket 里的 player ID 匹配)
                        # 这里做一个简化的遍历：在所有玩家的所有角色里找，通常自己的角色 ID 较小
                        for p in players:
                            for char in p.get("character", []):
                                if char.get("definitionId") == target_def_id:
                                    target_entity_id = char.get("id")
                                    print(Fore.GREEN + f"   🔍 找到角色 {target_def_id} -> 实体ID: {target_entity_id}")
                                    break
                            if target_entity_id: break
                    
                    # 如果没找到状态（比如第一帧），降级使用 Definition ID
                    final_id = target_entity_id if target_entity_id else target_def_id
                    
                    response_payload = {
                        "id": rpc_id,
                        "response": {
                            "setup": {
                                "characterId": final_id 
                            }
                        }
                    }

                # --- 发送响应 ---
                if response_payload:
                    print(Fore.YELLOW + f"🚀 发送响应 RPC {rpc_id}: {response_payload}")
                    asyncio.create_task(self.send_action(response_payload))
                
                return

            # ==========================================
            # 3. 📥 更新状态 (Notification)
            # ==========================================
            if evt_type == "notification":
                state = evt_data.get("state", {})
                if state:
                    self.latest_state = state  # <--- [新增] 记忆状态
                    
                    # 打印一些调试信息
                    phase = state.get("phase")
                    print(Fore.BLUE + f"ℹ️ [状态更新] Phase: {phase}")

            elif evt_type == "gameStart":
                print(Fore.GREEN + "✨✨✨ 游戏正式开始! ✨✨✨")
            
            elif evt_type == "oppTimer":
                pass 
            
            else:
                pass

        except Exception as e:
            print(Fore.RED + f"⚠️ 解析异常: {e}")
            import traceback
            traceback.print_exc()

async def main():
    bot = GenshinTCGBot()
    
    # 1. 登录并创建房间
    if await bot.login_guest():
        # 2. 如果成功，开始监听
        await bot.listen_to_game()
    else:
        print(Fore.RED + "⛔ 程序终止")
        
"""
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n👋 用户手动中断")
"""