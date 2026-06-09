from __future__ import annotations

import random

import logfire

from schemas import Place


class ClusteringService:
    """Groups places into day-clusters that are both geographically coherent and
    balanced in the number of places per day.

    Objectives (in priority order):
    1. Balance:   Every day gets a near-equal number of places (sizes differ by
                  at most one) and no day is left empty.
    2. Coherence: Subject to that balance, keep places close to each other on the
                  same day to minimise travel.

    This is a size-constrained ("balanced") K-means: K-means++ finds day centroids,
    then each place is assigned to the nearest centroid that still has free capacity.
    Every place is preserved exactly and appears in exactly one cluster.
    """

    @staticmethod
    def cluster(
        places: list[Place],
        num_days: int,
        random_seed: int = 42,
    ) -> list[list[Place]]:
        """Group places into `num_days` balanced, geographically coherent clusters.

        Produces exactly `min(num_days, len(places))` non-empty clusters whose
        sizes differ by at most one place. When there are fewer places than days,
        one cluster is produced per place (each day cannot otherwise be filled).
        """
        with logfire.span(
            "cluster {place_count} places into {day_count} days",
            place_count=len(places),
            day_count=num_days,
        ) as span:
            if not places:
                span.set_attribute("cluster_sizes", [])
                return []

            k = max(1, min(num_days, len(places)))
            if k == 1:
                span.set_attribute("cluster_sizes", [len(places)])
                return [list(places)]

            coords = [ClusteringService._coord(p) for p in places]
            labels = ClusteringService._balanced_kmeans(
                coords, k, random_seed=random_seed
            )

            clusters: list[list[Place]] = [[] for _ in range(k)]
            for place, label in zip(places, labels):
                clusters[label].append(place)

            span.set_attribute("cluster_count", k)
            span.set_attribute("cluster_sizes", [len(c) for c in clusters])
            return clusters

    # ------------------------------------------------------------------
    # Balanced K-means core
    # ------------------------------------------------------------------

    @staticmethod
    def _balanced_kmeans(
        coords: list[tuple[float, float]],
        k: int,
        max_iter: int = 100,
        random_seed: int = 42,
    ) -> list[int]:
        """Size-constrained K-means with k-means++ initialisation.

        Like Lloyd's algorithm, but the assignment step respects per-cluster
        capacities so the resulting clusters are balanced in size.
        """
        rng = random.Random(random_seed)
        n = len(coords)

        capacities = ClusteringService._target_sizes(n, k)
        centroids = ClusteringService._init_centroids_pp(coords, k, rng)
        labels = [0] * n

        for _ in range(max_iter):
            new_labels = ClusteringService._assign_balanced(
                coords, centroids, capacities
            )

            if new_labels == labels:
                break
            labels = new_labels

            for j in range(k):
                pts = [coords[i] for i in range(n) if labels[i] == j]
                if pts:
                    centroids[j] = (
                        sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts),
                    )

        return labels

    @staticmethod
    def _target_sizes(n: int, k: int) -> list[int]:
        """Per-cluster capacities summing to `n` that differ by at most one.

        The first `n % k` clusters take the larger size. Since `n >= k`, every
        capacity is at least one, so no cluster can end up empty.
        """
        base, remainder = divmod(n, k)
        return [base + 1 if j < remainder else base for j in range(k)]

    @staticmethod
    def _assign_balanced(
        coords: list[tuple[float, float]],
        centroids: list[tuple[float, float]],
        capacities: list[int],
    ) -> list[int]:
        """Assign every place to the nearest centroid with remaining capacity.

        Greedily fills the globally closest (place, cluster) pairs first, so each
        place lands on its nearest day that is not yet full. A final pass places
        any leftover into whatever capacity remains, guaranteeing a full, balanced
        assignment.
        """
        n = len(coords)
        k = len(centroids)

        pairs = sorted(
            (ClusteringService._sq_dist(coords[i], centroids[j]), i, j)
            for i in range(n)
            for j in range(k)
        )

        labels = [-1] * n
        counts = [0] * k
        for _dist, i, j in pairs:
            if labels[i] == -1 and counts[j] < capacities[j]:
                labels[i] = j
                counts[j] += 1

        # Fallback: capacities sum to n, so any unassigned place has a free slot.
        for i in range(n):
            if labels[i] == -1:
                j = next(j for j in range(k) if counts[j] < capacities[j])
                labels[i] = j
                counts[j] += 1

        return labels

    @staticmethod
    def _init_centroids_pp(
        coords: list[tuple[float, float]],
        k: int,
        rng: random.Random,
    ) -> list[tuple[float, float]]:
        """K-means++ centroid initialisation for better cluster quality."""
        centroids = [coords[0]]

        for _ in range(1, k):
            sq_dists = [
                min(ClusteringService._sq_dist(p, c) for c in centroids) for p in coords
            ]
            total = sum(sq_dists)
            if total == 0:
                centroids.append(coords[len(centroids) % len(coords)])
                continue

            threshold = rng.random() * total
            cumulative = 0.0
            chosen = coords[-1]
            for i, d in enumerate(sq_dists):
                cumulative += d
                if cumulative >= threshold:
                    chosen = coords[i]
                    break
            centroids.append(chosen)

        return centroids

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coord(place: Place) -> tuple[float, float]:
        if place.gps_coordinates:
            return (place.gps_coordinates.latitude, place.gps_coordinates.longitude)
        return (0.0, 0.0)

    @staticmethod
    def _sq_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
