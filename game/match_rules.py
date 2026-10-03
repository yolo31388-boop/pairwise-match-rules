"""
比赛规则系统 - 核心模块

修复内容：
- round_end(): 回合结束后正确换边，并重置装备与经济
- overtime(): 加时赛有明确胜利条件，进入时重置经济并换边，且保证终止
- tie_breaker(): 平局处理基于回合差规则，真正平局时触发加时赛而非随机判定
- match_flow(): 状态互斥、带倒计时过渡、暂停/继续时计时器同步暂停
- score_tracking(): 实时记录击杀/死亡/伤害，比分达到上限比赛立即结束
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Config:
    """配置项"""
    rounds_to_win: int = 16
    max_rounds: int = 30
    overtime_rounds_to_win: int = 4
    overtime_max_rounds: int = 6
    starting_money: int = 800
    overtime_money: int = 10000
    round_time: float = 115.0
    transition_time: float = 5.0


class MatchRules:
    """比赛规则系统核心类"""

    # 互斥的比赛状态
    PHASE_IDLE = "idle"
    PHASE_COUNTDOWN = "countdown"
    PHASE_ROUND_LIVE = "round_live"
    PHASE_ROUND_END = "round_end"
    PHASE_PAUSED = "paused"
    PHASE_MATCH_END = "match_end"

    # 队伍与阵营
    TEAM_A = "A"
    TEAM_B = "B"
    SIDE_ATTACK = "attack"
    SIDE_DEFENSE = "defense"

    DEFAULT_CONFIG: Dict = {
        "rounds_to_win": 16,
        "max_rounds": 30,
        "overtime_rounds_to_win": 4,
        "overtime_max_rounds": 6,
        "starting_money": 800,
        "overtime_money": 10000,
        "round_time": 115.0,
        "transition_time": 5.0,
    }

    def __init__(self, config: Optional[Dict] = None):
        self.config = dict(self.DEFAULT_CONFIG)
        if config:
            self.config.update(config)
        self._state = {}
        self._history = []
        self.players: Dict[str, Dict] = {}
        self._init_match_state()

    # ------------------------------------------------------------------
    # 初始化 / 重置
    # ------------------------------------------------------------------
    def _init_match_state(self):
        """初始化（或重新初始化）一整场比赛的状态"""
        self.phase = self.PHASE_IDLE
        self._pre_pause_phase = None
        self.scores = {"A": 0, "B": 0}
        self.sides = {"A": self.SIDE_ATTACK, "B": self.SIDE_DEFENSE}
        self.round_number = 0
        self.overtime = False
        self.overtime_rounds_played = 0
        self._overtime_base = {"A": 0, "B": 0}
        self.winner = None
        self.round_timer = 0.0
        self.phase_timer = 0.0
        for player in self.players.values():
            player["money"] = self.config["starting_money"]
            player["equipment"] = []
            player["stats"] = {"kills": 0, "deaths": 0, "damage": 0}

    def reset(self):
        """重置状态"""
        self._state = {}
        self._history = []
        self._init_match_state()

    def add_player(self, name: str, team: str) -> bool:
        """注册玩家到指定队伍"""
        if team not in (self.TEAM_A, self.TEAM_B):
            raise ValueError("队伍必须是 'A' 或 'B'")
        if name in self.players:
            return False
        self.players[name] = {
            "team": team,
            "money": self.config["starting_money"],
            "equipment": [],
            "stats": {"kills": 0, "deaths": 0, "damage": 0},
        }
        return True

    # ------------------------------------------------------------------
    # 比赛流程状态机（状态互斥 + 过渡 + 暂停）
    # ------------------------------------------------------------------
    def start_match(self) -> bool:
        """开始比赛，先进入开场倒计时"""
        if self.phase != self.PHASE_IDLE:
            return False
        self.round_number = 1
        self._enter_phase(self.PHASE_COUNTDOWN)
        return True

    def pause(self) -> bool:
        """暂停比赛（仅倒计时或回合进行中可暂停），计时器冻结"""
        if self.phase not in (self.PHASE_COUNTDOWN, self.PHASE_ROUND_LIVE):
            return False
        self._pre_pause_phase = self.phase
        self.phase = self.PHASE_PAUSED
        return True

    def resume(self) -> bool:
        """继续比赛，恢复到暂停前的状态"""
        if self.phase != self.PHASE_PAUSED or self._pre_pause_phase is None:
            return False
        self.phase = self._pre_pause_phase
        self._pre_pause_phase = None
        return True

    def update(self, dt: float):
        """每帧更新。暂停/结束状态下计时器不动"""
        if dt is None or dt < 0:
            return
        if self.phase == self.PHASE_PAUSED:
            return
        if self.phase == self.PHASE_COUNTDOWN:
            self.phase_timer -= dt
            if self.phase_timer <= 0:
                self.phase = self.PHASE_ROUND_LIVE
                self.round_timer = float(self.config["round_time"])
        elif self.phase == self.PHASE_ROUND_END:
            self.phase_timer -= dt
            if self.phase_timer <= 0:
                self.round_number += 1
                self._enter_phase(self.PHASE_COUNTDOWN)
        elif self.phase == self.PHASE_ROUND_LIVE:
            self.round_timer -= dt
            if self.round_timer <= 0:
                # 回合时间耗尽：防守方获胜
                defender = self._team_on_side(self.SIDE_DEFENSE)
                self.round_end(defender)

    def _enter_phase(self, phase: str):
        """带倒计时的状态过渡入口"""
        self.phase = phase
        self.phase_timer = float(self.config["transition_time"])

    # ------------------------------------------------------------------
    # 回合结束：换边 + 重置装备经济
    # ------------------------------------------------------------------
    def round_end(self, winner: str) -> bool:
        """回合结束。只能在回合进行中调用，返回是否生效"""
        if self.phase != self.PHASE_ROUND_LIVE:
            return False
        if winner not in (self.TEAM_A, self.TEAM_B):
            raise ValueError("获胜方必须是 'A' 或 'B'")
        if self.winner is not None:
            return False

        self.scores[winner] += 1
        self._history.append({
            "round": self.round_number,
            "winner": winner,
            "overtime": self.overtime,
        })
        if self.overtime:
            self.overtime_rounds_played += 1

        if self._check_match_decided():
            return True

        # 比赛继续：重置装备与经济，然后换边
        self._reset_loadouts(self._current_start_money())
        self.switch_sides()
        self._enter_phase(self.PHASE_ROUND_END)
        return True

    def switch_sides(self):
        """进攻方与防守方互换"""
        self.sides["A"], self.sides["B"] = self.sides["B"], self.sides["A"]

    def _team_on_side(self, side: str) -> str:
        return self.TEAM_A if self.sides["A"] == side else self.TEAM_B

    def _reset_loadouts(self, money: int):
        """重置所有玩家的装备与经济"""
        for player in self.players.values():
            player["equipment"] = []
            player["money"] = money

    def _current_start_money(self) -> int:
        if self.overtime:
            return int(self.config["overtime_money"])
        return int(self.config["starting_money"])

    # ------------------------------------------------------------------
    # 胜负判定 / 平局处理 / 加时赛
    # ------------------------------------------------------------------
    def _check_match_decided(self) -> bool:
        """检查比赛是否已有结果。有结果则收尾并返回 True"""
        score_a = self.scores["A"]
        score_b = self.scores["B"]

        if self.overtime:
            cap = int(self.config["overtime_rounds_to_win"])
            ot_a = score_a - self._overtime_base["A"]
            ot_b = score_b - self._overtime_base["B"]
            if ot_a >= cap or ot_b >= cap:
                self._finish_match("A" if ot_a > ot_b else "B")
                return True
            if self.overtime_rounds_played >= int(self.config["overtime_max_rounds"]):
                # 加时赛轮次用尽，保证比赛终止：领先则获胜，平分则平局
                if ot_a == ot_b:
                    self._finish_match("draw")
                else:
                    self._finish_match("A" if ot_a > ot_b else "B")
                return True
            return False

        cap = int(self.config["rounds_to_win"])
        if score_a >= cap or score_b >= cap:
            self._finish_match("A" if score_a > score_b else "B")
            return True
        if score_a + score_b >= int(self.config["max_rounds"]):
            self.tie_breaker()
            return True
        return False

    def tie_breaker(self) -> Dict:
        """
        平局处理（规则判定，不随机）：
        - 回合差不为 0：回合数多的一方获胜
        - 回合差为 0：触发加时赛
        """
        score_a = self.scores["A"]
        score_b = self.scores["B"]
        diff = score_a - score_b
        if diff != 0:
            self._finish_match("A" if diff > 0 else "B")
            return {"result": "win", "winner": "A" if diff > 0 else "B",
                    "round_diff": abs(diff)}
        self.start_overtime()
        return {"result": "overtime", "winner": None, "round_diff": 0}

    def start_overtime(self):
        """进入加时赛：重置经济与装备并换边"""
        self.overtime = True
        self.overtime_rounds_played = 0
        self._overtime_base = {"A": self.scores["A"], "B": self.scores["B"]}
        self._reset_loadouts(int(self.config["overtime_money"]))
        self.switch_sides()
        self._enter_phase(self.PHASE_COUNTDOWN)

    def _finish_match(self, result: str):
        """比赛收尾。result 为 'A'、'B' 或 'draw'"""
        self.winner = result
        self.phase = self.PHASE_MATCH_END
        self.round_timer = 0.0
        self.phase_timer = 0.0

    # ------------------------------------------------------------------
    # 装备 / 经济 / 实时数据
    # ------------------------------------------------------------------
    def give_equipment(self, player_name: str, item: str, cost: int = 0) -> bool:
        """购买装备（扣除金钱）。金钱不足则失败"""
        player = self.players.get(player_name)
        if player is None:
            return False
        if cost > player["money"]:
            return False
        player["money"] -= cost
        player["equipment"].append(item)
        return True

    def record_kill(self, attacker: str, victim: str, damage: int = 100) -> bool:
        """实时记录一次击杀：击杀数、死亡数、伤害立即更新"""
        if attacker not in self.players or victim not in self.players:
            return False
        self.players[attacker]["stats"]["kills"] += 1
        self.players[attacker]["stats"]["damage"] += damage
        self.players[victim]["stats"]["deaths"] += 1
        return True

    def record_damage(self, player_name: str, amount: int) -> bool:
        """实时记录伤害"""
        player = self.players.get(player_name)
        if player is None:
            return False
        player["stats"]["damage"] += amount
        return True

    def get_score(self) -> Dict[str, int]:
        """获取当前比分（实时）"""
        return {"A": self.scores["A"], "B": self.scores["B"]}

    def get_player_stats(self, player_name: str) -> Optional[Dict]:
        """获取玩家详细数据：击杀/死亡/伤害"""
        player = self.players.get(player_name)
        if player is None:
            return None
        return dict(player["stats"])

    @property
    def score_cap(self) -> int:
        """当前赛制的比分上限"""
        if self.overtime:
            return max(self._overtime_base.values()) + int(self.config["overtime_rounds_to_win"])
        return int(self.config["rounds_to_win"])
