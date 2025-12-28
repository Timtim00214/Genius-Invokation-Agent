# core/serializer.py
from google.protobuf.json_format import MessageToDict
from proto_compiled import rpc_pb2 # 确保你已经编译了 proto 文件

# ==========================================
# Part 1: Decoder (Server -> Client)
# 用于将 SSE 收到的 Protobuf 二进制/对象转为字典供 AI 分析
# ==========================================

def proto_to_dict(proto_obj):
    """
    最强转换器：将任何 Protobuf 对象转为 Python 字典。
    保留默认值，保留枚举名称。
    """
    return MessageToDict(
        proto_obj,
        including_default_value_fields=True, # 即使是0或空也显示，方便调试
        preserving_proto_field_name=False,   # 设为 False 以自动转为 camelCase (符合前端/JSON习惯)
        use_integers_for_enums=False         # 显示枚举的名字(如 DICE_OMNI) 而非数字
    )

# ==========================================
# Part 2: Serializer (Client -> Server)
# 严格对应 rpc.proto 中的 message Response
# ==========================================

class Serializer:
    """
    构造发送给服务器的 Response Payload。
    注意：这里的方法名对应 rpc.proto 中 'message Response' 的 oneof 字段。
    """

    @staticmethod
    def switch_hands(removed_hand_ids: list[int]):
        """
        Phase 0: 初始换牌
        对应 rpc.proto: SwitchHandsResponse
        Payload: { "switchHands": { "removedHandIds": [1, 2] } }
        """
        return {
            "switchHands": {
                "removedHandIds": removed_hand_ids
            }
        }

    @staticmethod
    def reroll_dice(dice_to_reroll: list[int]):
        """
        Phase 1: 投掷阶段重投骰子
        对应 rpc.proto: RerollDiceResponse
        Payload: { "rerollDice": { "diceToReroll": [0, 1] } }
        注意：dice_to_reroll 应该是 DiceType 枚举的整数值 (或者枚举名字符串，视服务端解析而定，通常整数最稳)
        """
        return {
            "rerollDice": {
                "diceToReroll": dice_to_reroll
            }
        }

    @staticmethod
    def choose_active(active_character_id: int):
        """
        切换出战角色 (死亡切换或开局选择)
        对应 rpc.proto: ChooseActiveResponse
        Payload: { "chooseActive": { "activeCharacterId": 1101 } }
        """
        return {
            "chooseActive": {
                "activeCharacterId": active_character_id
            }
        }

    @staticmethod
    def perform_action(chosen_action_index: int, used_dice: list[int]):
        """
        核心战斗行动：出牌、攻击、结束回合
        对应 rpc.proto: ActionResponse
        
        关键逻辑：
        服务器在 SSE 中会发送 'ActionRequest'，里面包含了一个 'repeated Action' 列表。
        AI 只需要决定执行列表中的第几个动作 (index)，并指定消耗哪些骰子。
        
        Payload: { "action": { "chosenActionIndex": 0, "usedDice": [1, 1, 2] } }
        """
        return {
            "action": {
                "chosenActionIndex": chosen_action_index,
                "usedDice": used_dice
            }
        }

    @staticmethod
    def select_card(selected_definition_id: int):
        """
        特殊效果选择 (如发现机制)
        对应 rpc.proto: SelectCardResponse
        Payload: { "selectCard": { "selectedDefinitionId": 311308 } }
        """
        return {
            "selectCard": {
                "selectedDefinitionId": selected_definition_id
            }
        }
