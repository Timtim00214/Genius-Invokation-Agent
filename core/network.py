import sys
import os
import asyncio
import json
import httpx
from httpx_sse import aconnect_sse
import logging
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
logger = logging.getLogger("CyborgAgent")

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
        # 🟢 修改点：将 timeout=10.0 改为 timeout=None
        # 这意味着 client 默认处于“长连接模式”，适合 SSE
        self.client = httpx.AsyncClient(base_url=base_url, timeout=None) 
        self.token = None
        self.player_id = None
        self.room_id = None

    async def login_guest(self, name="Cyborg_001"):
        """ 
        创建房间 (登录)
        对应服务端 Endpoint: POST /rooms 
        """
        print(Fore.YELLOW + f"🚀 正在发起连接... [Target: {self.base_url}]")
        
        # 核心 Payload 构造 (根据抓包分析修正)
        # 结构：{ name, password, config: {gameVersion...}, deck: {...} }
        payload = {
            "name": name,
            "password": "",  # 密码通常为空字符串
            "config": {
                "gameVersion": 24,  # [关键] 必须 >= 24 (由抓包 deck.requiredVersion 确定)
                "isPvp": False,     # False = 人机/测试模式
                "botId": 0          # 0 = 默认
            },
            "deck": SAMPLE_DECK
        }

        try:
            # 🟢 修改点：在这里手动加上 timeout=10.0
            # POST 是短连接，如果 10秒 没反应就是真挂了
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
                print(Fore.CYAN + f"   🔑 Token: {self.token[:15]}...")
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
        """
        向服务器发送操作指令
        Endpoint: POST /rooms/{roomId}/play (通常是这个，或者是 /action)
        """
        if not self.token or not self.room_id:
            print(Fore.RED + "❌ 无法发送指令: 未连接房间")
            return False

        url = f"/rooms/{self.room_id}/play" # 如果报错404，尝试改成 /action
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        print(Fore.YELLOW + f"📤 正在发送指令 Payload: {json.dumps(payload, indent=None)}")

        try:
            # 动作指令必须快速响应，设置 5秒 超时防止死锁
            resp = await self.client.post(url, json=payload, headers=headers, timeout=5.0)
            
            if resp.status_code == 200 or resp.status_code == 201:
                print(Fore.GREEN + f"✅ 指令发送成功!")
                return True
            else:
                print(Fore.RED + f"❌ 指令发送失败 ({resp.status_code}): {resp.text}")
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

            # 🎯 核心关注点：notification (包含完整的 State)
            if evt_type == "notification":
                real_data = event.get("data", {})
                state = real_data.get("state", {})
                
                if not state:
                    return

                # --- 提取关键战术指标 ---
                phase = state.get("phase", "Unknown")
                round_num = state.get("roundNumber", 0)
                players = state.get("player", [])

                # 寻找我自己 (假设我们是 Host，通常是 index 0，但也可能是 1)
                # 简单的判断逻辑：谁的手牌 definitionId 不为 0，谁就是我
                my_idx = 0
                if len(players) > 1:
                    # 检查玩家 0 的第一张手牌，如果是 0，说明我看不到，那我应该是玩家 1
                    p0_hand = players[0].get("handCard", [])
                    if p0_hand and p0_hand[0].get("definitionId") == 0:
                        my_idx = 1
                
                me = players[my_idx]
                
                # --- 打印清爽的仪表盘 ---
                print(Fore.YELLOW + "="*50)
                print(Fore.YELLOW + f"🔥 [回合 {round_num}] 阶段: {phase} | 我是: P{my_idx}")
                print(Fore.YELLOW + "="*50)

                # 1. 显示前台角色
                active_char_id = me.get("activeCharacterId")
                print(Fore.CYAN + f"🦸 前台角色实体ID: {active_char_id}")
                
                # 2. 显示骰子
                dice = me.get("dice", [])
                print(Fore.MAGENTA + f"🎲 元素骰 ({len(dice)}): {dice}")

                # 3. 显示手牌 (只显示 ID，方便调试)
                hand = me.get("handCard", [])
                hand_ids = [c.get("definitionId") for c in hand]
                print(Fore.GREEN + f"🃏 手牌 ({len(hand)}): {hand_ids}")

                # 4. 检查是否需要我行动
                # 这是一个简单的启发式判断
                # 实际上我们需要根据 Phase 和 Turn 来判断
                current_turn = state.get("currentTurn", -1)
                if current_turn == my_idx:
                    print(Fore.RED + "⚡⚡⚡ 轮到我行动! (YOUR TURN) ⚡⚡⚡")
                else:
                    print(Fore.WHITE + "💤 等待对手行动...")

            elif evt_type == "gameStart":
                print(Fore.GREEN + "✨✨✨ 游戏正式开始! ✨✨✨")
            
            elif evt_type == "oppTimer":
                pass # 忽略倒计时
            
            else:
                # 其他杂项消息简略显示
                print(Fore.BLUE + f"ℹ️ [Event] {evt_type}")

        except Exception as e:
            print(Fore.RED + f"⚠️ 解析异常: {e}")

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