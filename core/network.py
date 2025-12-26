# core/network.py
import sys
import os

# --- 🔗 路径黑魔法 V2 (彻底解决依赖地狱) ---
# 1. 获取当前文件绝对路径
current_file_path = os.path.abspath(__file__)
# 2. 项目根目录 (invokation-agent/)
project_root = os.path.dirname(os.path.dirname(current_file_path))
# 3. Proto 编译目录 (invokation-agent/proto_compiled/)
proto_dir = os.path.join(project_root, "proto_compiled")

# 4. 【关键步骤】同时挂载 根目录 和 Proto目录
# 挂载 Proto 目录，解决 rpc_pb2 内部 import enums_pb2 找不到的问题
sys.path.insert(0, proto_dir) 
# 挂载 根目录，方便引用 config.py 等其他模块
sys.path.insert(1, project_root)

print(f"🔧 路径修复完成:")
print(f"  -> {proto_dir}")
print(f"  -> {project_root}")
# ----------------------------------------

import asyncio
import websockets
import logging

# 现在你可以直接 import rpc_pb2 了，不需要加前缀，
# 因为 proto_compiled 已经在搜索路径里了
try:
    import rpc_pb2
    # 顺便测试一下它依赖的 enums_pb2 是否也能找到
    import enums_pb2 
    print("✅ 成功加载所有 Proto 定义文件 (rpc + enums)")
except ImportError as e:
    print(f"❌ 还是报错: {e}")
    sys.exit(1)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GameClient:
    def __init__(self, server_url):
        self.uri = server_url
        self.websocket = None
        self.running = False

    async def connect(self):
        logger.info(f"🔌 正在尝试连接服务器: {self.uri}")
        try:
            async with websockets.connect(self.uri) as websocket:
                self.websocket = websocket
                self.running = True
                logger.info("✅ 连接成功！等待战场数据...")
                await self.listen_loop()
        except Exception as e:
            logger.error(f"❌ 连接失败: {e}")
            logger.error("💡 提示：请确保你通过 'bun run dev' 启动了正确的 Server，并且 URL 正确")

    async def listen_loop(self):
        try:
            while self.running:
                message = await self.websocket.recv()
                msg_len = len(message)
                logger.info(f"📩 收到数据 | 长度: {msg_len} | Hex: {message[:20].hex()}...")
                
                # --- 尝试解析 (根据你的 rpc_pb2 内容调整) ---
                # 这一步通常需要反序列化。
                # 如果是第一条消息，可能是 ServerMessage
                try:
                    # 这是一个猜测，你需要打开 rpc_pb2.py 确认里面有没有 ServerMessage 类
                    # 或者可能是 notification_pb2.Notification
                    # parsed = rpc_pb2.ServerMessage()
                    # parsed.ParseFromString(message)
                    # logger.info(f"🔍 解析成功: {parsed}")
                    pass
                except:
                    pass

        except websockets.exceptions.ConnectionClosed:
            logger.info("🔌 连接已断开")

if __name__ == "__main__":
    # ⚠️ 这里的 URL 非常关键
    # 请务必在浏览器 F12 -> Network -> WS 里找到真实的 URL
    # 如果是本地 Server，通常是 ws://localhost:3000/api/game
    TEST_URL = "ws://localhost:3000/api/game" 
    
    try:
        asyncio.run(GameClient(TEST_URL).connect())
    except KeyboardInterrupt:
        print("停止运行")