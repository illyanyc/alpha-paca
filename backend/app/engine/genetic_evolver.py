"""Genetic algorithm strategy evolution with paper-trade validation gate."""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

POPULATION_SIZE = 20
MUTATION_RATE = 0.15
CROSSOVER_RATE = 0.6
ELITE_FRACTION = 0.2
IMPROVEMENT_THRESHOLD = 1.05
PAPER_VALIDATION_HOURS = 48


@dataclass
class StrategyGenome:
    """Encodes tunable strategy parameters as a genome."""

    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std_mult: float = 2.0
    stop_pct: float = 0.03
    target_mult_1: float = 1.5
    target_mult_2: float = 2.5
    entry_threshold: float = 0.1
    fitness: float = 0.0
    generation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rsi_period": self.rsi_period,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "macd_fast": self.macd_fast,
            "macd_slow": self.macd_slow,
            "macd_signal": self.macd_signal,
            "bb_period": self.bb_period,
            "bb_std_mult": self.bb_std_mult,
            "stop_pct": self.stop_pct,
            "target_mult_1": self.target_mult_1,
            "target_mult_2": self.target_mult_2,
            "entry_threshold": self.entry_threshold,
            "fitness": self.fitness,
            "generation": self.generation,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StrategyGenome:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class EvolutionResult:
    """Result of one evolution cycle."""

    best_genome: StrategyGenome
    generation: int
    population_size: int
    best_fitness: float
    avg_fitness: float
    promoted_to_paper: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_genome": self.best_genome.to_dict(),
            "generation": self.generation,
            "population_size": self.population_size,
            "best_fitness": round(self.best_fitness, 4),
            "avg_fitness": round(self.avg_fitness, 4),
            "promoted_to_paper": self.promoted_to_paper,
            "timestamp": self.timestamp.isoformat(),
        }


class GeneticEvolver:
    """Evolves strategy parameters using a genetic algorithm.

    Every cycle:
    1. Generate population from current params + N mutations
    2. Evaluate fitness via backtester
    3. Select top performers, crossover, mutate
    4. If best > current * IMPROVEMENT_THRESHOLD: promote to paper trading
    """

    def __init__(
        self,
        current_genome: StrategyGenome | None = None,
        backtester: Any | None = None,
    ) -> None:
        self._current = current_genome or StrategyGenome()
        self._backtester = backtester
        self._generation = 0
        self._history: list[EvolutionResult] = []
        self._paper_candidates: list[StrategyGenome] = []

    def evolve(self, market_data: Any | None = None) -> EvolutionResult:
        """Run one generation of evolution."""
        self._generation += 1
        population = self._create_population()

        for genome in population:
            genome.fitness = self._evaluate_fitness(genome, market_data)
            genome.generation = self._generation

        population.sort(key=lambda g: g.fitness, reverse=True)

        elite_count = max(1, int(len(population) * ELITE_FRACTION))
        elite = population[:elite_count]

        next_gen = list(elite)
        while len(next_gen) < POPULATION_SIZE:
            parent_a = self._tournament_select(population)
            parent_b = self._tournament_select(population)
            child = self._crossover(parent_a, parent_b)
            child = self._mutate(child)
            next_gen.append(child)

        best = next_gen[0]
        avg_fitness = np.mean([g.fitness for g in next_gen])
        promoted = False

        if best.fitness > self._current.fitness * IMPROVEMENT_THRESHOLD:
            self._paper_candidates.append(copy.deepcopy(best))
            promoted = True
            logger.info(
                "genome_promoted_to_paper",
                generation=self._generation,
                fitness=best.fitness,
                improvement=best.fitness / max(self._current.fitness, 1e-9),
            )

        result = EvolutionResult(
            best_genome=best,
            generation=self._generation,
            population_size=len(next_gen),
            best_fitness=best.fitness,
            avg_fitness=float(avg_fitness),
            promoted_to_paper=promoted,
        )
        self._history.append(result)
        return result

    def promote_to_live(self, genome: StrategyGenome) -> None:
        """Promote a paper-validated genome to live trading."""
        self._current = copy.deepcopy(genome)
        logger.info("genome_promoted_to_live", generation=genome.generation, fitness=genome.fitness)

    def get_current_genome(self) -> StrategyGenome:
        return self._current

    def get_paper_candidates(self) -> list[dict[str, Any]]:
        return [g.to_dict() for g in self._paper_candidates]

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._history[-limit:]]

    def _create_population(self) -> list[StrategyGenome]:
        population = [copy.deepcopy(self._current)]
        for _ in range(POPULATION_SIZE - 1):
            genome = copy.deepcopy(self._current)
            genome = self._mutate(genome)
            population.append(genome)
        return population

    def _evaluate_fitness(self, genome: StrategyGenome, market_data: Any) -> float:
        """Evaluate genome fitness. Uses backtester if available, else heuristic."""
        if self._backtester is not None and market_data is not None:
            try:
                result = self._backtester.run(genome.to_dict(), market_data)
                sharpe = result.get("sharpe", 0.0)
                trade_count = result.get("trade_count", 0)
                max_dd = result.get("max_drawdown", 1.0)
                return sharpe * np.sqrt(max(trade_count, 1)) * (1 - max_dd)
            except Exception:
                logger.warning("backtest_evaluation_failed")

        score = 0.0
        if 10 <= genome.rsi_period <= 20:
            score += 0.2
        if genome.macd_fast < genome.macd_slow:
            score += 0.2
        if 1.5 <= genome.bb_std_mult <= 2.5:
            score += 0.2
        if 0.02 <= genome.stop_pct <= 0.05:
            score += 0.2
        if genome.target_mult_1 < genome.target_mult_2:
            score += 0.2
        score += random.uniform(-0.1, 0.1)
        return max(score, 0.0)

    def _tournament_select(self, population: list[StrategyGenome], k: int = 3) -> StrategyGenome:
        contestants = random.sample(population, min(k, len(population)))
        return max(contestants, key=lambda g: g.fitness)

    def _crossover(self, a: StrategyGenome, b: StrategyGenome) -> StrategyGenome:
        if random.random() > CROSSOVER_RATE:
            return copy.deepcopy(a)

        child = StrategyGenome()
        fields = list(StrategyGenome.__dataclass_fields__.keys())
        for f in fields:
            if f in ("fitness", "generation"):
                continue
            val_a = getattr(a, f)
            val_b = getattr(b, f)
            if isinstance(val_a, (int, float)):
                if random.random() < 0.5:
                    setattr(child, f, val_a)
                else:
                    setattr(child, f, val_b)
        return child

    def _mutate(self, genome: StrategyGenome) -> StrategyGenome:
        g = copy.deepcopy(genome)

        if random.random() < MUTATION_RATE:
            g.rsi_period = max(5, min(30, g.rsi_period + random.randint(-3, 3)))
        if random.random() < MUTATION_RATE:
            g.rsi_overbought = max(60, min(85, g.rsi_overbought + random.uniform(-5, 5)))
        if random.random() < MUTATION_RATE:
            g.rsi_oversold = max(15, min(40, g.rsi_oversold + random.uniform(-5, 5)))
        if random.random() < MUTATION_RATE:
            g.macd_fast = max(5, min(20, g.macd_fast + random.randint(-2, 2)))
        if random.random() < MUTATION_RATE:
            g.macd_slow = max(15, min(40, g.macd_slow + random.randint(-3, 3)))
        if random.random() < MUTATION_RATE:
            g.macd_signal = max(5, min(15, g.macd_signal + random.randint(-2, 2)))
        if random.random() < MUTATION_RATE:
            g.bb_period = max(10, min(40, g.bb_period + random.randint(-3, 3)))
        if random.random() < MUTATION_RATE:
            g.bb_std_mult = max(1.0, min(3.5, g.bb_std_mult + random.uniform(-0.3, 0.3)))
        if random.random() < MUTATION_RATE:
            g.stop_pct = max(0.01, min(0.10, g.stop_pct + random.uniform(-0.01, 0.01)))
        if random.random() < MUTATION_RATE:
            g.target_mult_1 = max(0.5, min(3.0, g.target_mult_1 + random.uniform(-0.3, 0.3)))
        if random.random() < MUTATION_RATE:
            g.target_mult_2 = max(1.0, min(5.0, g.target_mult_2 + random.uniform(-0.3, 0.3)))
        if random.random() < MUTATION_RATE:
            g.entry_threshold = max(0.01, min(0.5, g.entry_threshold + random.uniform(-0.03, 0.03)))

        if g.macd_fast >= g.macd_slow:
            g.macd_slow = g.macd_fast + 5
        if g.target_mult_1 >= g.target_mult_2:
            g.target_mult_2 = g.target_mult_1 + 0.5

        return g
