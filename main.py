import sys
import os
import asyncio
import json
from colorama import Fore, Style, init

# ==========================================
# 🛠️ 环境路径修正
# ==========================================
current_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_path)
proto_dir = os.path.join(current_path, "proto_compiled")
sys.path.append(proto_dir)

# 导入核心模块
from core.network import GenshinTCGBot

# 初始化彩色输出
init(autoreset=True)

# ==========================================
# ⚙️ 房间规格预设 (已修正数值限制)
# ==========================================
ROOM_PRESETS = {
    "1": {
        "name": "⚡ 最小/极速 (Minimal)",
        "config": {
            "initTotalActionTime": 20,
            "rerollTime": 25,
            "roundTotalActionTime": 20,
            "actionTime": 25
        }
    },
    "2": {
        "name": "⚖️ 标准 (Standard) - 默认",
        "config": {
            "initTotalActionTime": 45,
            "rerollTime": 40,
            "roundTotalActionTime": 60,
            "actionTime": 25
        }
    },
    "3": {
        "name": "🐢 双倍/慢速 (Double)",
        "config": {
            "initTotalActionTime": 20,
            "rerollTime": 60,
            "roundTotalActionTime": 180,
            "actionTime": 45
        }
    },
    "4": {
        "name": "☕ 超长 (Super Long)",
        "config": {
            "initTotalActionTime": 60,
            "rerollTime": 120,
            "roundTotalActionTime": 300,
            "actionTime": 90
        }
    },
    "5": {
        "name": "♾️ 无尽/调试 (Endless)",
        "config": {
            # 🟢 [修正] 服务器最大只允许 300，超过会报 400 Bad Request
            "initTotalActionTime": 100,
            "rerollTime": 300,
            "roundTotalActionTime": 300, 
            "actionTime": 300
        }
    }
}

class SmartBot(GenshinTCGBot):
    def __init__(self):
        super().__init__()
        self.last_rpc_id = None      
        self.current_state = None    
        self.max_rpc_id_seen = -1 

    async def start_heartbeat(self):
        """ 防止 AttributeError 的心跳占位符 """
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    async def try_action(self):
        """ 尝试行动：基于 RPC ID 的绝对优先逻辑 """
        if self.last_rpc_id is None:
            return

        rpc_id = self.last_rpc_id
        state = self.current_state or {} 
        phase_raw = state.get("phase", "Unknown")

        print(Fore.MAGENTA + f"🧩 [决策流] RPC: {rpc_id} | Phase: {phase_raw}")

        # --- RPC 0: 换牌 ---
        if rpc_id == 0:
            print(Fore.YELLOW + f"🤖 [AI] 强制响应换牌 (RPC: 0)...")
            payload = {
                "id": rpc_id,
                "response": {"switchHands": {"removedHandIds": []}}
            }
            self.last_rpc_id = None 
            await asyncio.sleep(0.5)
            await self.send_action(payload)
            return

        # --- RPC 1: 选首发 (关键修复逻辑) ---
        if rpc_id == 1:
            print(Fore.YELLOW + f"🤖 [AI] 强制响应选人 (RPC: 1)...")
            
            target_entity_id = None
            if state:
                players = state.get("player", [])
                my_idx = 0 
                # 自动识别 P0/P1
                if len(players) > 1:
                    if players[0].get("handCard", []) and players[0]["handCard"][0].get("definitionId") == 0:
                        my_idx = 1
                
                me = players[my_idx]
                my_chars = me.get("characters", [])
                if my_chars:
                    # 必须用 id (Entity ID)
                    target_entity_id = my_chars[0].get("id")
                    print(Fore.GREEN + f"   ✅ 从状态中提取角色 Entity ID: {target_entity_id}")

            if target_entity_id is None:
                print(Fore.RED + "⚠️ 警告: 无法获取角色状态，尝试盲打 Entity ID: 1")
                target_entity_id = 1 

            payload = {
                "id": rpc_id,
                "response": {
                    "decideActive": { 
                        "activeId": target_entity_id
                    }
                }
            }
            self.last_rpc_id = None
            await asyncio.sleep(0.5)
            await self.send_action(payload)
            return

        # --- RPC 2~8: 投骰子 ---
        is_roll_phase = (phase_raw == "PHASE_ROLL" or phase_raw == 1 or "ROLL" in str(phase_raw).upper())
        if is_roll_phase:
            print(Fore.YELLOW + f"🤖 [AI] 响应重投 (RPC: {rpc_id})...")
            payload = {
                "id": rpc_id,
                "response": {"rerollDice": {"diceIndex": []}}
            }
            self.last_rpc_id = None
            await asyncio.sleep(1)
            await self.send_action(payload)
            return

        # --- 通用行动 ---
        print(Fore.RED + f"🤖 [AI] 通用行动响应 (RPC: {rpc_id})...")
        payload = {
            "id": rpc_id,
            "response": {"action": {"declareEnd": {}}}
        }
        self.last_rpc_id = None
        await asyncio.sleep(1)
        await self.send_action(payload)

    async def handle_game_event(self, raw_data):
        import json
        try:
            event = json.loads(raw_data)
        except:
            return 

        evt_type = event.get("type")
        evt_data = event.get("data", {})

        # 💀 游戏结束监听
        if evt_type == "gameEnd":
            print(Fore.RED + "\n" + "█"*50)
            print(Fore.RED + f"💀 游戏结束! 获胜者: {evt_data.get('winPlayerId')}")
            print(Fore.RED + f"❌ [判负原因]: {evt_data.get('reason')}")
            print(Fore.RED + f"📜 信息: {evt_data.get('message')}")
            print(Fore.RED + "█"*50 + "\n")
            return

        # ⚡ RPC 监听
        if evt_type == "rpc":
            rpc_id = event.get("id")
            if rpc_id is None: rpc_id = evt_data.get("id")
            self.last_rpc_id = rpc_id
            if rpc_id is not None:
                self.max_rpc_id_seen = max(self.max_rpc_id_seen, rpc_id)
                print(Fore.MAGENTA + f"⚡ [Event] ✅ 收到令牌 RPC: {self.last_rpc_id}")
                await self.try_action()
            return

        # 📡 Notification 监听
        if evt_type == "notification":
            state = evt_data.get("state", {})
            if state:
                self.current_state = state
                if self.last_rpc_id is not None:
                    await self.try_action()

async def main():
    bot = SmartBot()
    
    print(Fore.CYAN + "请选择房间规格 (官方配置):")
    for key, val in ROOM_PRESETS.items():
        print(f"  [{key}] {val['name']}")
    
    choice = input(Fore.CYAN + "请输入序号 (默认 5): ").strip()
    if not choice:
        choice = "5"
        
    selected_preset = ROOM_PRESETS.get(choice, ROOM_PRESETS["2"])
    print(Fore.GREEN + f"✅ 已选择: {selected_preset['name']}")
    
    # 登录
    if await bot.login_guest(custom_config=selected_preset["config"]):
        print(Fore.GREEN + "🚀 系统启动中...")
        
        # ✅ [找回功能] 生成调试链接
        if hasattr(bot, 'generate_debug_link'):
            bot.generate_debug_link()
        else:
            print(Fore.RED + "⚠️ 警告：generate_debug_link 未在 network.py 中定义，无法自动生成链接")

        # 创建任务
        listener_task = asyncio.create_task(bot.listen_to_game())
        heartbeat_task = asyncio.create_task(bot.start_heartbeat())
        
        try:
            await asyncio.wait(
                [listener_task, heartbeat_task], 
                return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            pass
    else:
        print(Fore.RED + "⛔ 程序终止")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n👋 用户手动中断")