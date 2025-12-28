import httpx
import asyncio
from colorama import Fore, init

init(autoreset=True)

BASE_URL = "http://localhost:3000/api"

async def inspect():
    async with httpx.AsyncClient() as client:
        print(Fore.YELLOW + "🕵️ 正在侦察服务端配置...")

        # 1. 抓取支持的游戏版本
        # 前端源码暗示可能在 /meta 或 /version
        try:
            # 尝试最常见的元数据接口
            resp = await client.get(f"{BASE_URL}/meta") 
            if resp.status_code != 200:
                resp = await client.get(f"{BASE_URL}/version")
            
            if resp.status_code == 200:
                data = resp.json()
                versions = data.get("supportedGameVersions", [])
                print(Fore.GREEN + f"✅ 服务端支持的版本: {versions}")
                
                # 告诉我最新的版本索引是多少
                latest_index = len(versions) - 1
                print(Fore.CYAN + f"💡 建议使用的 gameVersion 索引: {latest_index} (对应版本 {versions[latest_index]})")
            else:
                print(Fore.RED + f"❌ 获取版本失败: {resp.status_code}")
        except Exception as e:
            print(Fore.RED + f"❌ 侦察版本异常: {e}")

        # 2. 抓取该版本下的合法卡组
        # 前端逻辑是 GET /decks?requiredVersion={index}
        try:
            # 我们假设最新版本索引是 valid_ver_index
            # 如果上面失败了，我们盲猜一个 0
            target_ver = latest_index if 'latest_index' in locals() else 0
            
            print(Fore.YELLOW + f"🕵️ 正在获取版本 [{target_ver}] 的合法卡组...")
            resp = await client.get(f"{BASE_URL}/decks?requiredVersion={target_ver}")
            
            if resp.status_code == 200:
                decks_data = resp.json()
                decks = decks_data.get("data", [])
                
                if decks:
                    print(Fore.GREEN + f"✅ 找到 {len(decks)} 套合法卡组!")
                    first_deck = decks[0]
                    print(Fore.CYAN + f"🃏 推荐使用的合法卡组 ID: {first_deck['id']}")
                    print(Fore.CYAN + f"   名称: {first_deck['name']}")
                    print(Fore.CYAN + f"   完整 Payload: {first_deck}")
                    
                    # 重点：我们需要把这个合法卡组打印出来，替换掉 main.py 里的 SAMPLE_DECK
                    print(Fore.MAGENTA + "\n👇 请把下面的字典替换到 network.py 的 SAMPLE_DECK 中 👇")
                    print(str(first_deck))
                else:
                    print(Fore.RED + "❌ 该版本下没有公共卡组，请先在网页端创建一个！")
            else:
                print(Fore.RED + f"❌ 获取卡组失败: {resp.status_code}")

        except Exception as e:
            print(Fore.RED + f"❌ 侦察卡组异常: {e}")

if __name__ == "__main__":
    asyncio.run(inspect())