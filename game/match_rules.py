"""
比赛规则系统 - 核心模块

实现内容：
- 回合结束正确换边，并重置双方装备与经济
- 加时赛有明确胜利条件（先拿 4 分且领先 2 分），经济重置并换边
- 平局处理有规则：仅常规赛 15:15 触发加时，其余比分直接判定胜负
- 比赛流程状态互斥，带冻结时间/回合结束过渡，支持暂停与继续
- 比分追踪实时更新，记录击杀/死亡/伤害等详细数据，比分有上限
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


TEAM_A = "team_a"
TEAM_B = "team_b"
ATTACKER = "attacker"
DEFENDER = "defender"

DEFAULT_CONFIG = {
    # 常规赛：先拿 win_target 分且总回合数不超过 max_rounds，15:15 进入加时
    "win_target": 16,
    "max_rounds": 30,
    # 加时赛：先拿 ot_win_target 分且领先 ot_win_margin 分，3:3 进入下一个加时
    "ot_win_target": 4,
    "ot_win_margin": 2,
    "ot_rounds_per_half": 3,
    # 经济
    "starting_money": 800,
    "overtime_money": 10000,
    # 流程计时（秒）
    "freeze_time": 5.0,
    "round_time": 115.0,
    "round_end_time": 5.0,
}


class Phase(Enum):
    """比赛流程状态（互斥）"""
    WARMUP = "warmup"
    FREEZE_TIME = "freeze_time"
    ROUND_IN_PROGRESS = "round_in_progress"
    ROUND_END = "round_end"
    MATCH_END = "match_end"


@dataclass
class Player:
    """玩家状态：装备、经济、位置与详细数据"""
    player_id: str
    team: str
    money: int = 0
    equipment: List[str] = field(default_factory=list)
    position: tuple = (0.0, 0.0)
    alive: bool = True
    kills: int = 0
    deaths: int = 0
    damage: float = 0.0

    def reset_loadout(self, money: int):
        """重置装备与经济（回合开始时调用）"""
        self.money = money
        self.equipment = []
        self.alive = True


class MatchRules:
    """比赛规则系统核心类"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = dict(DEFAULT_CONFIG)
        if config:
            self.config.update(config)
        self._state = {}
        self._history = []
        self.players: Dict[str, Player] = {}
        self.phase = Phase.WARMUP
        self.paused = False
        self.round_number = 0
        self.overtime_number = 0
        self.score = {TEAM_A: 0, TEAM_B: 0}
        self.ot_score = {TEAM_A: 0, TEAM_B: 0}
        self.sides = {TEAM_A: ATTACKER, TEAM_B: DEFENDER}
        self.winner: Optional[str] = None
        self._round_timer = 0.0
        self._phase_timer = 0.0

    # ------------------------------------------------------------------
    # 玩家与流程控制
    # ------------------------------------------------------------------

    def add_player(self, player_id: str, team: str) -> Player:
        if team not in (TEAM_A, TEAM_B):
            raise ValueError(f"未知队伍: {team}")
        player = Player(player_id=player_id, team=team,
                        money=self.config["starting_money"])
        self.players[player_id] = player
        return player

    def start_match(self):
        """从热身进入第 1 回合（冻结时间过渡）"""
        if self.phase != Phase.WARMUP:
            raise RuntimeError(f"当前状态 {self.phase.value} 不能开始比赛")
        self._start_round()

    def start_round(self):
        """冻结时间结束后手动进入回合进行中"""
        if self.phase != Phase.FREEZE_TIME:
            raise RuntimeError(f"当前状态 {self.phase.value} 不能开始回合")
        self._enter_round_in_progress()

    def pause(self):
        """暂停比赛：所有计时器停止"""
        if self.phase in (Phase.WARMUP, Phase.MATCH_END):
            raise RuntimeError(f"当前状态 {self.phase.value} 不能暂停")
        self.paused = True

    def resume(self):
        """继续比赛：计时器从暂停处继续"""
        self.paused = False

    def update(self, dt: float):
        """每帧更新：驱动状态过渡；暂停时计时器不前进"""
        if dt < 0:
            raise ValueError("dt 不能为负")
        if self.paused or self.phase in (Phase.WARMUP, Phase.MATCH_END):
            return
        if self.phase == Phase.FREEZE_TIME:
            self._phase_timer -= dt
            if self._phase_timer <= 0:
                self._enter_round_in_progress()
        elif self.phase == Phase.ROUND_IN_PROGRESS:
            self._round_timer -= dt
            if self._round_timer <= 0:
                # 回合时间耗尽，防守方获胜
                defender = self._team_of_side(DEFENDER)
                self.round_end(defender, reason="time_expired")
        elif self.phase == Phase.ROUND_END:
            self._phase_timer -= dt
            if self._phase_timer <= 0:
                self._advance_from_round_end()

    # ------------------------------------------------------------------
    # 回合结束：换边 + 重置装备经济
    # ------------------------------------------------------------------

    def round_end(self, winning_team: str, reason: str = ""):
        """回合结束：计分、判定比赛/加时胜负、换边并重置装备经济"""
        if self.phase != Phase.ROUND_IN_PROGRESS:
            raise RuntimeError(f"当前状态 {self.phase.value} 不能结束回合")
        if winning_team not in (TEAM_A, TEAM_B):
            raise ValueError(f"未知队伍: {winning_team}")

        self.score[winning_team] += 1
        if self.overtime_number > 0:
            self.ot_score[winning_team] += 1
        self._history.append({
            "round": self.round_number,
            "overtime": self.overtime_number,
            "winner": winning_team,
            "reason": reason,
            "score": dict(self.score),
        })

        match_winner = self._check_match_winner()
        if match_winner is not None:
            self.winner = match_winner
            self.phase = Phase.MATCH_END
            return

        # 换边：常规赛每回合后换边；加时赛每个半场（3 回合）后换边
        if self._should_swap_sides():
            self._swap_sides()

        # 重置装备与经济，玩家不得带着上一回合的装备打下一回合
        self._reset_loadouts()

        self.phase = Phase.ROUND_END
        self._phase_timer = float(self.config["round_end_time"])

    def _should_swap_sides(self) -> bool:
        if self.overtime_number == 0:
            return True
        return self._ot_rounds_played() % self.config["ot_rounds_per_half"] == 0

    def _swap_sides(self):
        for team in self.sides:
            self.sides[team] = DEFENDER if self.sides[team] == ATTACKER else ATTACKER

    def _reset_loadouts(self):
        money = (self.config["overtime_money"] if self.overtime_number > 0
                 else self.config["starting_money"])
        for player in self.players.values():
            player.reset_loadout(money)

    def _check_match_winner(self) -> Optional[str]:
        if self.overtime_number > 0:
            # 加时赛胜利条件基于加时赛得分：先拿 ot_win_target 分且领先 ot_win_margin 分
            a, b = self.ot_score[TEAM_A], self.ot_score[TEAM_B]
            target = self.config["ot_win_target"]
            margin = self.config["ot_win_margin"]
            if max(a, b) >= target and abs(a - b) >= margin:
                return TEAM_A if a > b else TEAM_B
            return None
        a, b = self.score[TEAM_A], self.score[TEAM_B]
        target = self.config["win_target"]
        if a >= target or b >= target:
            return TEAM_A if a > b else TEAM_B
        if a + b >= self.config["max_rounds"] and a != b:
            return TEAM_A if a > b else TEAM_B
        return None

    # ------------------------------------------------------------------
    # 平局处理与加时赛
    # ------------------------------------------------------------------

    def tie_breaker(self) -> str:
        """平局处理：仅常规赛打平（15:15）时触发加时赛，否则按规则判定"""
        if self.phase == Phase.MATCH_END:
            return "match_over"
        if self.phase != Phase.ROUND_END:
            raise RuntimeError(f"当前状态 {self.phase.value} 不能进行平局处理")
        if self.overtime_number > 0:
            # 加时赛半场打平（3:3）：进入下一个加时赛；否则继续本加时赛
            if (self._ot_rounds_played()
                    >= 2 * self.config["ot_rounds_per_half"]
                    and self.ot_score[TEAM_A] == self.ot_score[TEAM_B]):
                self._start_overtime()
                return "overtime"
            return "continue"
        a, b = self.score[TEAM_A], self.score[TEAM_B]
        if a == b:
            # 平局：只有打满常规回合数（15:15）才算真正平局并触发加时；
            # 回合数未满（如 1:1）不算平局，继续下一回合
            if a + b >= self.config["max_rounds"]:
                self._start_overtime()
                return "overtime"
            return "continue"
        # 回合数打满仍非平局：回合差更大的一方获胜，比赛结束
        if a + b >= self.config["max_rounds"]:
            self.winner = TEAM_A if a > b else TEAM_B
            self.phase = Phase.MATCH_END
            return "match_over"
        return "continue"

    def overtime(self) -> str:
        """加时赛状态：返回 'overtime' 或 'match_over'"""
        if self.overtime_number == 0:
            raise RuntimeError("当前不在加时赛中")
        return "match_over" if self.phase == Phase.MATCH_END else "overtime"

    def _start_overtime(self):
        """进入加时赛：换边、重置加时经济、比分继续累计"""
        self.overtime_number += 1
        self.ot_score = {TEAM_A: 0, TEAM_B: 0}
        self._swap_sides()
        self._reset_loadouts()
        self._start_round()

    def _ot_rounds_played(self) -> int:
        return self.ot_score[TEAM_A] + self.ot_score[TEAM_B]

    # ------------------------------------------------------------------
    # 比分追踪：实时更新 + 详细数据
    # ------------------------------------------------------------------

    def record_kill(self, killer_id: str, victim_id: str):
        """实时记录击杀/死亡"""
        killer = self.players[killer_id]
        victim = self.players[victim_id]
        killer.kills += 1
        victim.deaths += 1
        victim.alive = False

    def record_damage(self, player_id: str, amount: float):
        """实时记录伤害"""
        if amount < 0:
            raise ValueError("伤害值不能为负")
        self.players[player_id].damage += amount

    def score_tracking(self) -> Dict:
        """实时比分快照：比分、回合、阶段与每位玩家的详细数据"""
        return {
            "score": dict(self.score),
            "ot_score": dict(self.ot_score),
            "round_number": self.round_number,
            "overtime_number": self.overtime_number,
            "phase": self.phase.value,
            "paused": self.paused,
            "sides": dict(self.sides),
            "winner": self.winner,
            "round_time_remaining": self._round_timer,
            "players": {
                pid: {
                    "team": p.team,
                    "kills": p.kills,
                    "deaths": p.deaths,
                    "damage": p.damage,
                    "money": p.money,
                    "equipment": list(p.equipment),
                    "alive": p.alive,
                }
                for pid, p in self.players.items()
            },
        }

    def match_flow(self) -> Dict:
        """比赛流程状态快照"""
        return {
            "phase": self.phase.value,
            "paused": self.paused,
            "round_number": self.round_number,
            "overtime_number": self.overtime_number,
            "round_time_remaining": self._round_timer,
            "phase_time_remaining": self._phase_timer,
            "winner": self.winner,
        }

    # ------------------------------------------------------------------
    # 购买与内部工具
    # ------------------------------------------------------------------

    def buy(self, player_id: str, item: str, price: int):
        """冻结时间内购买装备"""
        if self.phase != Phase.FREEZE_TIME:
            raise RuntimeError("只能在冻结时间内购买装备")
        player = self.players[player_id]
        if player.money < price:
            raise ValueError("金钱不足")
        player.money -= price
        player.equipment.append(item)

    def reset(self):
        """重置状态"""
        self.__init__(self.config)

    def _start_round(self):
        self.round_number += 1
        self.phase = Phase.FREEZE_TIME
        self._phase_timer = float(self.config["freeze_time"])
        for player in self.players.values():
            player.position = (0.0, 0.0)

    def _enter_round_in_progress(self):
        self.phase = Phase.ROUND_IN_PROGRESS
        self._round_timer = float(self.config["round_time"])

    def _advance_from_round_end(self):
        """回合结束过渡完成：平局则触发加时，否则进入下一回合"""
        if self.tie_breaker() == "match_over":
            return
        if self.phase == Phase.ROUND_END:
            self._start_round()

    def _team_of_side(self, side: str) -> str:
        for team, team_side in self.sides.items():
            if team_side == side:
                return team
        raise RuntimeError(f"没有队伍处于 {side} 方")
