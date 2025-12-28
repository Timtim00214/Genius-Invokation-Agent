# main.py最顶部
import sys
import os

# 1. 把项目根目录加入路径，解决 'from core...' 报错
current_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_path)

# 2. 把 proto_compiled 加入路径，解决 proto 报错
proto_dir = os.path.join(current_path, "proto_compiled")
sys.path.append(proto_dir)

# 3. 然后才是导入模块
import asyncio
from colorama import Fore
from core.network import GenshinTCGBot  # 从 core.network 导入
from core.serializer import Serializer



class SmartBot(GenshinTCGBot):
    """
    智能版 Bot，继承基础的网络功能，增加了决策逻辑
    """
    
    async def handle_game_event(self, raw_data):
        """
        重写父类的处理方法：不仅看，还要动！
        """
        # 1. 先调用父类方法打印漂亮的仪表盘 (可选，为了看清日志)
        await super().handle_game_event(raw_data)
        
        # 2. 解析数据用于决策
        import json
        event = json.loads(raw_data)
        if event.get("type") != "notification":
            return

        state = event.get("data", {}).get("state", {})
        if not state:
            return

        phase = state.get("phase")
        
        # ==========================================
        # 🧠 AI 决策核心 (简单版)
        # ==========================================
        
        # 场景 A: 游戏刚开始，Phase 0 (换牌阶段)
        # 识别特征: phase 包含 "ChangeHands" 或 "Init" (视具体枚举字符串而定)
        # 我们的策略: 不换牌，直接确认
        if phase == "PHASE_CHANGE_HANDS" or phase == "PHASE_INIT": 
            print(Fore.YELLOW + "🤖 AI 决策: 收到换牌请求，决定不换牌...")
            
            # 使用 Serializer 构造 Payload
            payload = Serializer.switch_hands(removed_hand_ids=[])
            
            # 发送!
            await asyncio.sleep(1) # 拟人化延迟
            await self.send_action(payload)
            return

        # 场景 B: 投骰子阶段 (Roll Phase)
        # 识别特征: phase == "PHASE_ROLL"
        if phase == "PHASE_ROLL":
            print(Fore.YELLOW + "🤖 AI 决策: 收到重投请求，决定保留所有骰子...")
            payload = Serializer.reroll_dice(dice_to_reroll=[])
            await asyncio.sleep(1)
            await self.send_action(payload)
            return

        # 场景 C: 战斗阶段 (Action Phase)
        # 识别特征: 轮到我了 (state.currentTurn == my_index)
        # 这里的判断逻辑需要和 network.py 里的一致
        players = state.get("player", [])
        my_idx = 0 
        # (简化逻辑: 如果我是P1且手牌可见，否则P2。这里直接沿用 network.py 的逻辑)
        if len(players) > 1 and players[0].get("handCard", []) and players[0]["handCard"][0]["definitionId"] == 0:
            my_idx = 1
            
        current_turn = state.get("currentTurn", -1)
        
        if current_turn == my_idx and phase == "PHASE_ACTION":
            print(Fore.RED + "🤖 AI 决策: 轮到我行动了！正在思考...")
            
            # --- 读取服务器允许的动作列表 ---
            # 服务端通常会在 state 或 request 字段里告诉你能干嘛
            # 假设 state 里没给，我们尝试“结束回合”作为兜底
            # 实际上 rpc.proto 里的 ActionRequest 会包含 repeated Action
            
            # 策略: 暂时只会“结束回合” (Declare End)
            # 真正的 AI 需要解析 state['validActions'] (如果存在)
            
            # 这里先测试最简单的：结束回合
            # 注意：根据 rpc.proto，我们需要发送的是 ActionResponse
            # 选择 index 0 (通常第一个动作是有效的)，消耗空骰子
            
            # ⚠️ 临时测试：尝试结束回合 
            # 如果服务端发来了 valid actions 列表，通常 结束回合 是最后一个
            # 这里我们需要 Serializer.perform_action
            
            print(Fore.YELLOW + "🤖 AI 决策: 暂时还没学会打牌，先尝试结束回合/空过")
            # 假设 action index 0 是有效的 (盲猜)
            payload = Serializer.perform_action(chosen_action_index=0, used_dice=[])
            
            await asyncio.sleep(2)
            await self.send_action(payload)

async def main():
    bot = SmartBot()
    if await bot.login_guest():
        await bot.listen_to_game()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass