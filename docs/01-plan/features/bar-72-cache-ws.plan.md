# BAR-72 — Redis 캐시 + WS 채널 샤딩

- BAR-72a (worktree): InMemoryCache + RedisCache (lazy) + WebSocketChannelShard (md5) + 13 tests
- BAR-72b (운영): Redis 클러스터 + Postgres 읽기 복제 + 실 P95 측정

## FR
- CacheLayer Protocol + InMemory (TTL+max_size) + Redis (SecretStr)
- WebSocketChannelShard md5 user_id 분배 (8 샤드 default)
- 회귀 ≥ 517
