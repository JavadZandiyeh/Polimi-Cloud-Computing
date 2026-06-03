from __future__ import annotations

import random

from schemas import Place

MAX_HOURS_PER_DAY = 8.0
DEFAULT_DURATION_HOURS = 1.5

# Estimated visit duration in hours keyed by lowercase type keyword
_TYPE_DURATIONS: dict[str, float] = {
    "museum": 2.0, "gallery": 2.0,
    "historical": 2.5, "ruins": 2.0, "monument": 1.0,
    "church": 1.0, "cathedral": 1.5, "temple": 1.0, "mosque": 1.0,
    "park": 1.5, "garden": 1.5, "nature": 2.0, "forest": 2.0,
    "restaurant": 1.0, "cafe": 0.75, "bar": 0.75, "food": 1.0,
    "shopping": 1.5, "market": 1.0, "mall": 2.0,
    "stadium": 2.5, "sport": 2.0,
    "beach": 2.5, "lake": 2.0,
    "zoo": 2.5, "aquarium": 2.0, "amusement": 4.0,
    "theater": 2.5, "cinema": 2.0,
    "viewpoint": 0.5, "lookout": 0.5,
}


class ClusteringService:
    """Groups places into geographically coherent day-clusters using K-means.

    Objectives (in priority order):
    1. Primary:   Minimise travel distance within each day (K-means on coordinates).
    2. Secondary: Produce exactly one cluster per trip day (capped at #places).
    3. Tertiary:  Avoid clusters too large for a realistic day by rebalancing
                  based on estimated visit durations.
    """

    @staticmethod
    def cluster(
        places: list[Place],
        num_days: int,
        random_seed: int = 42,
        max_hours_per_day: float = MAX_HOURS_PER_DAY,
    ) -> list[list[Place]]:
        """Group places into `num_days` geographically coherent clusters.

        Runs K-means++ on GPS coordinates then rebalances overloaded clusters
        using estimated visit durations so no day exceeds `max_hours_per_day`.
        Every place is preserved exactly and appears in exactly one cluster.
        """
        if not places:
            return []

        k = max(1, min(num_days, len(places)))
        if k == 1:
            return [list(places)]

        coords = [
            (p.gps_coordinates.latitude, p.gps_coordinates.longitude)
            if p.gps_coordinates else (0.0, 0.0)
            for p in places
        ]
        labels = ClusteringService._kmeans(coords, k, random_seed=random_seed)

        clusters: list[list[Place]] = [[] for _ in range(k)]
        for place, label in zip(places, labels):
            clusters[label].append(place)

        clusters = [c for c in clusters if c]
        clusters = ClusteringService._rebalance(clusters, max_hours_per_day)

        return clusters

    # ------------------------------------------------------------------
    # Duration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_duration(place: Place) -> float:
        """Estimate visit duration in hours from place type labels."""
        if place.type:
            for label in place.type:
                label_lower = label.lower()
                for keyword, hours in _TYPE_DURATIONS.items():
                    if keyword in label_lower:
                        return hours
        return DEFAULT_DURATION_HOURS

    @staticmethod
    def _cluster_duration(cluster: list[Place]) -> float:
        return sum(ClusteringService.estimate_duration(p) for p in cluster)

    # ------------------------------------------------------------------
    # Rebalancing (tertiary objective)
    # ------------------------------------------------------------------

    @staticmethod
    def _centroid(cluster: list[Place]) -> tuple[float, float]:
        coords = [
            (p.gps_coordinates.latitude, p.gps_coordinates.longitude)
            if p.gps_coordinates else (0.0, 0.0)
            for p in cluster
        ]
        return (
            sum(c[0] for c in coords) / len(coords),
            sum(c[1] for c in coords) / len(coords),
        )

    @staticmethod
    def _rebalance(
        clusters: list[list[Place]],
        max_hours: float,
        max_passes: int = 20,
    ) -> list[list[Place]]:
        """Move places from overloaded clusters to less loaded neighbours.

        In each pass, for every cluster whose estimated duration exceeds
        `max_hours`, the place farthest from that cluster's centroid is
        relocated to the geographically nearest other cluster. Repeats until
        no cluster is overloaded or `max_passes` is reached.
        """
        for _ in range(max_passes):
            moved = False
            for i, cluster in enumerate(clusters):
                if ClusteringService._cluster_duration(cluster) <= max_hours:
                    continue
                if len(cluster) <= 1:
                    continue

                centroid = ClusteringService._centroid(cluster)
                farthest_idx = max(
                    range(len(cluster)),
                    key=lambda j: ClusteringService._sq_dist(
                        (cluster[j].gps_coordinates.latitude, cluster[j].gps_coordinates.longitude)
                        if cluster[j].gps_coordinates else (0.0, 0.0),
                        centroid,
                    ),
                )
                place = cluster[farthest_idx]
                place_coord = (
                    (place.gps_coordinates.latitude, place.gps_coordinates.longitude)
                    if place.gps_coordinates else (0.0, 0.0)
                )

                best_j = min(
                    (j for j in range(len(clusters)) if j != i and clusters[j]),
                    key=lambda j: ClusteringService._sq_dist(
                        place_coord, ClusteringService._centroid(clusters[j])
                    ),
                    default=None,
                )
                if best_j is None:
                    continue

                cluster.pop(farthest_idx)
                clusters[best_j].append(place)
                moved = True

            if not moved:
                break

        return clusters

    # ------------------------------------------------------------------
    # K-means core
    # ------------------------------------------------------------------

    @staticmethod
    def _kmeans(
        coords: list[tuple[float, float]],
        k: int,
        max_iter: int = 100,
        random_seed: int = 42,
    ) -> list[int]:
        """K-means with k-means++ initialisation on (lat, lng) coordinates."""
        rng = random.Random(random_seed)
        n = len(coords)

        centroids = ClusteringService._init_centroids_pp(coords, k, rng)
        labels = [0] * n

        for _ in range(max_iter):
            new_labels = [
                min(range(k), key=lambda j, p=point: ClusteringService._sq_dist(p, centroids[j]))
                for point in coords
            ]

            if new_labels == labels:
                break
            labels = new_labels

            new_centroids = []
            for j in range(k):
                pts = [coords[i] for i in range(n) if labels[i] == j]
                if pts:
                    new_centroids.append((
                        sum(p[0] for p in pts) / len(pts),
                        sum(p[1] for p in pts) / len(pts),
                    ))
                else:
                    new_centroids.append(centroids[j])
            centroids = new_centroids

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
                min(ClusteringService._sq_dist(p, c) for c in centroids)
                for p in coords
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

    @staticmethod
    def _sq_dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
