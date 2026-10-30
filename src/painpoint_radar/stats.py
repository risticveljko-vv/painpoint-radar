"""Pure thread-vote statistics. Not a pipeline step; imported by sentiment."""

from collections import Counter
import math
import random


def vader_vote(compounds: list[float], pos: float = 0.05, neg: float = -0.05) -> str:
    """Classify the mean compound using inclusive VADER thresholds."""
    if not compounds:
        return "neu"
    mean = math.fsum(compounds) / len(compounds)
    return "pos" if mean >= pos else "neg" if mean <= neg else "neu"


def rubric_vote(score: int) -> str:
    """Map a five-point rubric score to a nominal vote."""
    if score not in (1, 2, 3, 4, 5):
        raise ValueError("score must be in 1..5")
    return "neg" if score <= 2 else "neu" if score == 3 else "pos"


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Return nominal agreement; empty or unequal lists are invalid."""
    if len(a) != len(b) or not a:
        raise ValueError("ratings must be nonempty and have equal lengths")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    counts_a, counts_b = Counter(a), Counter(b)
    expected = sum(count * counts_b[label] for label, count in counts_a.items()) / (n * n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Return a Wilson score interval for k successes in n trials."""
    if n < 0 or not 0 <= k <= n or z <= 0:
        raise ValueError("require 0 <= k <= n and z > 0")
    if n == 0:
        return (0.0, 1.0)
    share = k / n
    denominator = 1 + z * z / n
    center = (share + z * z / (2 * n)) / denominator
    half = z * math.sqrt(share * (1 - share) / n + z * z / (4 * n * n)) / denominator
    return (0.0 if k == 0 else max(0.0, center - half),
            1.0 if k == n else min(1.0, center + half))


def _vote_values(votes: list[str]) -> list[int]:
    values = {"pos": 1, "neu": 0, "neg": -1}
    if any(vote not in values for vote in votes):
        raise ValueError("votes must be pos, neu, or neg")
    return [values[vote] for vote in votes]


def bootstrap_net(votes: list[str], iters: int = 2000, seed: int = 0) -> tuple[float, float]:
    """Return linearly interpolated 2.5/97.5 percentiles; empty -> (-1, 1)."""
    if iters <= 0:
        raise ValueError("iters must be positive")
    values = _vote_values(votes)
    n = len(values)
    if not n:
        return (-1.0, 1.0)
    rng = random.Random(seed)
    samples = sorted(sum(rng.choices(values, k=n)) / n for _ in range(iters))

    def percentile(p: float) -> float:
        index = (iters - 1) * p
        lower = math.floor(index)
        upper = math.ceil(index)
        return samples[lower] + (samples[upper] - samples[lower]) * (index - lower)

    return (percentile(0.025), percentile(0.975))


def aggregate(votes: list[str]) -> dict:
    """Summarize one vote per thread; empty shares and net are zero."""
    values = _vote_values(votes)
    counts = Counter(votes)
    n = len(votes)
    return {
        "n": n,
        "pos": counts["pos"],
        "neu": counts["neu"],
        "neg": counts["neg"],
        "net": sum(values) / n if n else 0.0,
        "pos_share": counts["pos"] / n if n else 0.0,
        "neg_share": counts["neg"] / n if n else 0.0,
        "wilson_pos": wilson(counts["pos"], n),
        "wilson_neg": wilson(counts["neg"], n),
        "net_ci": bootstrap_net(votes),
    }


def report_allowed(n: int, kappa: float, min_n: int = 10, min_kappa: float = 0.4) -> tuple[bool, str]:
    """Require enough threads and agreement; success has an empty reason."""
    if n < min_n:
        return (False, f"n={n} is below minimum {min_n}")
    if not kappa >= min_kappa:
        return (False, f"kappa={kappa} is below minimum {min_kappa}")
    return (True, "")


if __name__ == "__main__":
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
