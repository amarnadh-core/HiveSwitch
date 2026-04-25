# Minecraft Serverless Server Architecture Specification (V1)

## Project Goal

Create a hybrid multiplayer system for Minecraft that behaves like a server without requiring a permanently running dedicated server.

A world is hosted by one active player at a time. When the current host leaves or disconnects, another player automatically becomes the new host and continues running the same world seamlessly.

Unlike a traditional dedicated server:

- The world simulation runs on a player's PC
- World data is stored in cloud storage
- Host responsibility can migrate between players
- The world can persist even when nobody is online
- Players only download world data they actually need

---

# Scope of V1

## Supported Features

- 2–4 players maximum
- Vanilla Minecraft only
- Hidden dedicated server process for hosting
- One active host at a time
- Automatic host migration
- Manual host request option
- Region-file-based syncing
- Cloud world storage
- Automatic reconnect after migration
- Partial chunk download and caching
- VPN-based networking using ZeroTier-like solution
- Freeze gameplay during migration
- Warm standby hosts
- Compression and integrity validation

## Explicitly Out of Scope for V1

- Forge support
- Heavy modpacks
- Seamless no-freeze migration
- Deterministic lockstep simulation
- STUN/TURN NAT traversal
- True P2P networking
- More than 4 players
- Distributed chunk ownership
- Complex permissions/roles beyond owner

---

# Overall Architecture

The system is split into 3 major local components plus cloud infrastructure.

## 1. Helper Application (.exe)

This is the main controller application.

Responsibilities:

- Join VPN network automatically
- Launch Minecraft normally through existing launcher(normal minecraft launcher or Tlauncher)
- Launch hidden dedicated server when needed
- Manage local cache
- Download/upload world files
- Track host quality score
- Select backup hosts
- Handle migration state
- Validate file hashes
- Perform cache cleanup
- Communicate with cloud backend
- Communicate with Minecraft mod

The Helper is not a full Minecraft launcher.
It is a sidekick utility that works alongside any standard Minecraft launcher.

## Local IPC Protocol

The Helper and Mod communicate locally using:

- Localhost WebSocket for real-time events
- Localhost HTTP API for requests
- JSON messages with version field

Example real-time events:

- migration\_started
- migration\_progress
- migration\_finished
- host\_changed
- reconnect\_required
- portal\_preload\_begin
- portal\_preload\_complete
- cache\_corruption\_detected
- host\_score\_updated

Example HTTP endpoints:

- GET /session
- GET /host-score
- GET /migration-state
- POST /request-host
- POST /portal-entered
- POST /migration-ack
- POST /player-ready

All IPC messages should include:

- Protocol version
- Session ID
- Timestamp
- Request ID
- Error code if relevant

## 2. Minecraft Mod (.jar)

A lightweight Fabric mod installed into the mods folder.

Responsibilities:

- Show migration UI
- Show reconnect UI
- Show loading indicators
- Freeze player movement during migration
- Allow camera/chat/UI during migration
- Detect portal usage
- Trigger preload requests
- Notify Helper about game events
- Receive state changes from Helper

The mod should remain lightweight and avoid handling heavy networking logic.

## 3. Hidden Dedicated Server

When a player becomes host, the Helper launches a hidden Minecraft dedicated server process in the background.

Responsibilities:

- Simulate the world
- Accept player connections
- Save region files
- Run panic save on crash
- Continue running briefly if client crashes

The host's Minecraft client connects to localhost.

This design is chosen because it is more stable than using the integrated singleplayer server.

## 4. Cloud Infrastructure

Responsibilities:

- Store world files
- Store metadata
- Track host ownership
- Track region versions
- Track snapshots
- Coordinate migration
- Validate cache versions

---

# Networking Strategy

## V1 Networking Method

V1 uses VPN-based networking.

Preferred options:

- ZeroTier
- Radmin VPN
- Tailscale later if needed

The Helper should automatically:

- Connect user to VPN network when session starts
- Disconnect user when session ends

## NAT Traversal

Not included in V1.

Future roadmap:

1. VPN-based networking
2. Relay server fallback
3. STUN/TURN with relay fallback

---

# Hosting Model

## Active Host

Only one player can be the active host at any time.

The active host:

- Runs hidden dedicated server
- Generates new chunks
- Saves world state
- Uploads region changes
- Handles entity simulation

## Warm Standby Hosts

The top 2 backup host candidates are kept partially prepared.

Warm standby responsibilities:

- Keep recent world metadata
- Cache important region files
- Keep server runtime ready
- Maintain updated host score

Warm standby state machine:

1. Idle

- No preparation
- Minimal resource usage

2. Pre-Warming

- Download latest metadata
- Validate cached regions
- Update host score
- Verify VPN connectivity

3. Standby

- Keep required server runtime available
- Keep important regions cached
- Keep migration metadata ready
- Remain ready for promotion

4. Promoting

- Download latest deltas
- Launch hidden dedicated server
- Validate session ownership
- Prepare reconnect target

5. Active

- Become current host
- Begin lease renewal
- Begin region syncing

This reduces migration time while avoiding excessive standby resource usage.

---

# Host Selection Logic

## Host Score Categories

Host selection uses a dynamic weighted scoring system.

Metrics:

- Ping to other players
- Upload bandwidth
- Packet loss
- CPU performance
- Free RAM
- SSD vs HDD
- Ethernet vs Wi-Fi
- Plugged-in vs battery
- Battery percentage
- Cached world percentage
- NAT quality
- Recent crash history

## Dynamic Weighting

Weights are adjusted dynamically.

Examples:

- Low battery increases importance of power state
- Large world size increases importance of SSD speed and cache completeness
- Urgent migration increases importance of cached world data
- Weak network increases importance of upload bandwidth and packet loss

## Host Score Display

Players should see simplified values such as:

- Host Quality: High
- Network: Excellent
- Host Score: 84/100

Detailed raw metrics should remain hidden.

---

# Host Migration Flow

## Migration Trigger

Migration begins when:

- Host disconnects
- Host crashes
- Host lease expires
- Host manually transfers control

## Host Lease

- Lease duration: 30 seconds
- Lease renew interval: 5 seconds
- Heartbeat interval: 2 seconds
- Grace period before migration: 5–10 seconds
- Require 2–3 consecutive missed renewals before migration begins

Host state transitions:

- Healthy
- Unstable
- Lost

If heartbeat packets stop arriving but lease still exists:

- Mark host as Unstable
- Continue attempting renewal
- Retry renewals with exponential backoff

Migration only begins if:

- Heartbeats are missing for around 10 seconds
- Multiple lease renewals are missed
- Grace period expires

## Migration Steps

1. Detect host failure
2. Freeze gameplay
3. Save emergency state
4. Select new host
5. Download latest metadata
6. Launch hidden dedicated server on new host
7. Sync latest regions
8. Reconnect players automatically
9. Resume gameplay

## Migration Freeze Rules

Allowed:

- Camera movement
- Chat
- UI access
- Viewing inventory

Blocked:

- Physical movement
- Inventory changes
- Crafting
- Portal usage
- Teleports
- Item movement
- Combat

## Reconnect Behavior

- Automatic reconnect for 30 seconds
- If reconnect fails after 30 seconds, show manual reconnect button

Migration UI should clearly communicate:

- Host migration in progress
- Name of next host
- Estimated remaining time
- Progress bar
- Reason for migration
- Retry button if reconnect takes too long

This is important because players may otherwise think the game has frozen or crashed.

---

# World Storage Strategy

## World Structure

Cloud storage uses normal Minecraft world folder layout.

Example:

```text
world/
  level.dat
  region/
  playerdata/
  DIM-1/
  DIM1/
  data/
```

## Snapshot Policy

Keep:

- Last 3 rolling snapshots
- One emergency recovery snapshot

Emergency snapshot can only be applied if owner approves.

## Snapshot Frequency

- Chunk changes synced every 10 seconds
- Player inventory synced every 5 seconds
- Full snapshot every 3 minutes
- Emergency save on disconnect

---

# Region Sync Strategy

## Region Storage Method

Use Minecraft region files (.mca).

No chunk-level storage in V1.

## Region Dirty Tracking

Primary method:

- Hook into Minecraft save events

Fallback methods:

- File timestamps
- File hashes

## Region Versioning

Each region stores:

- Integer version number
- SHA-256 hash

Example:

```text
r.0.0.mca -> version 15
r.0.1.mca -> version 8
```

## Dirty Region Queue

Each dirty region entry contains:

- Region ID
- Timestamp
- Priority
- Retry count
- Upload status
- Current version
- Hash

Dirty regions are uploaded using dynamic batching.

## Upload Priority Rules

Priority is based on:

1. Distance to active players
2. Recently modified regions
3. Portal-adjacent chunks
4. Spawn chunks
5. Active dimensions

## Compression

Use zstd compression for uploads and downloads.

Reason:

- Better compression than gzip
- Faster decompression
- Lower bandwidth usage

---

# Chunk Download Strategy

Players do not download the full world immediately.

They only download:

- Player inventory data
- Player metadata
- Nearby chunks
- Spawn chunks
- Relevant portal destination chunks

## Initial Join Radius

- Hard cap: 10 chunks
- Load around 60–70% of visible nearby chunks before allowing player to spawn
- Remaining chunks load in background

## On-Demand Downloading

Chunks are downloaded only when needed.

Example:

- If player never visits a distant base, those regions are never downloaded
- If player enters a portal, destination chunks begin preloading

## Portal Preload Radius

- Initial preload radius: 5 chunks
- Further chunks load in background

Portal transitions are intentionally allowed to take slightly longer to hide loading.

## Chunk Generation Ownership

Only the active host can generate brand-new unexplored chunks.

Clients are never allowed to independently generate unexplored terrain.

Reason:

- Prevent terrain mismatch
- Prevent world divergence
- Prevent chunk duplication

---

# Cache Strategy

## Local Cache

Players keep local cache of:

- Region files
- Metadata
- Previously visited dimensions
- World snapshots

## Cache Limit

- Default cache size: 2 GB
- User configurable
- Active worlds are protected from cleanup
- Prompt user to increase limit if actual usage approaches cap

The Helper UI should show:
- Actual cache usage
- Configured cache limit
- Explanation that cache stores world chunks locally for faster loading

## Cache Cleanup

Use automatic cleanup with LRU-style behavior.

## Cache Reuse Rules

Reuse cache whenever possible.

Do NOT reuse cache if:

- Region hash mismatch
- Corrupted file detected
- Minecraft version changed
- Mod hash changed
- Registry IDs changed
- World generation rules changed

## Player Data Rule

Player inventory and playerdata files should never be reused.

These files are always downloaded fresh when player joins.

Reason:

- Prevent ghost inventories
- Prevent duplication
- Prevent stale player state

---

# Disconnect and Crash Recovery

## Host Disconnect

- Wait 5–10 seconds
- If host reconnects quickly, continue normally
- Otherwise migrate

## Hidden Server Crash Policy

If client crashes:

- Hidden dedicated server remains alive for about 20 seconds
- Attempts panic save
- Attempts emergency upload
- Allows client to reconnect if possible

## Temporarily Absent Players

If player disconnects during migration:

- Their entity remains frozen for 5 minutes
- Their inventory remains preserved
- They can rejoin seamlessly

## Inventory Conflict Policy

Accept small rollback risk.

Prefer simplicity over perfect consistency.

Expected rollback window:
- Usually 0–5 seconds
- Worst case tied to 5 second inventory sync interval

The Helper and Mod UI should communicate:
- Last successful inventory sync time
- Warning if inventory changes are not yet fully synced
- Small notice after migration if rollback may have occurred

Reason:
- Players who understand rollback are less likely to think the game is broken

---

# Integrity and Anti-Corruption

Strict integrity validation is enabled.

## Version Handshake Rules

Before joining a session, all players must match on:

- Helper version
- Mod version
- Minecraft version
- World protocol version
- World mod hash

If any mismatch is detected:

- Joining is blocked
- User is shown mismatch reason
- User is prompted to update or repair

Reason:

- Prevent world corruption
- Prevent cache mismatch
- Prevent incompatible region syncing

## Validation Methods

- SHA-256 hashes
- Atomic uploads
- Temporary staging uploads
- Validation before activation
- Rollback on failed upload

## Corruption Handling

If corruption is detected:

- Region is redownloaded
- Poisoned cache is deleted
- Emergency snapshot can be used

---

# Dimension Rules

Priority order:

1. Current dimension with most active players
2. Portal destination chunks
3. Spawn chunks
4. Other active-player dimensions
5. Inactive dimensions

Portal logic is suspended during migration.

If an entity enters a portal during migration:

- Velocity is frozen
- Position is frozen
- Portal transfer waits
- Entity resumes after migration finishes

---

# Ownership Rules

## World Owner

The owner can:

- Delete world
- Reset world
- Approve emergency recovery snapshot
- Transfer ownership
- Manually choose host

## Manual Host Request

Any player can request to become host.

Flow:

1. Player requests host
2. Current host sees request
3. Current host approves or denies
4. If approved, migration begins

## Ownership Transfer

- Owner can nominate successor manually
- If owner inactive for 1.5 months, vote-based transfer can happen later

---

# Cloud Backend

## Suggested Backend Stack for Development

Recommended low-cost development stack:

- Supabase for metadata database and authentication
- Cloudflare R2 or Backblaze B2 for world storage
- PostgreSQL for persistent metadata
- Redis later for temporary session cache if needed

## Metadata Database Stores

- World IDs
- Session IDs
- Region versions
- Player cache versions
- Snapshot history
- Host ownership
- Lease status
- Warm standby state
- World protocol version

## API Responsibilities

The backend API should provide:

- Session creation
- Session join
- Lease renewal
- Host election
- Region version lookup
- Snapshot lookup
- Upload authorization
- Download authorization
- Ownership transfer
- Emergency recovery request

## Atomic Host Election

Host election must use atomic locking.

Reason:
- Prevent split-brain scenarios
- Prevent two standby hosts becoming active at the same time
- Prevent world corruption

Recommended PostgreSQL strategy:
- Use SELECT FOR UPDATE or advisory locks
- Store current host lease row
- Only one standby host may acquire promotion lock
- Promotion lock expires automatically if host fails to complete promotion

Host election flow:
1. Detect expired host lease
2. Acquire database lock
3. Validate lease still expired
4. Select highest-ranked standby host
5. Mark standby host as Promoting
6. Issue temporary promotion token
7. Release lock after promotion succeeds

## Upload Authorization

Clients should never store long-lived cloud storage credentials.

Correct flow:
- Helper requests short-lived upload URL from backend
- Backend validates player is current active host
- Backend returns presigned upload URL
- Helper uploads region file directly to object storage

Non-host players:
- Only receive presigned read URLs
- Cannot receive upload permissions

## Authentication

The hidden dedicated server should run in online mode.

Requirements:

- Legitimate Minecraft accounts
- Mojang/Microsoft authentication
- Session server validation

Reason:

- Prevent spoofing
- Prevent offline-mode cheating
- Stay compatible with Mojang multiplayer rules

# Development Phases

## Phase 1: Basic Prototype

Features:

- ZeroTier networking
- Manual host selection
- Basic world upload/download
- Basic hidden server launch
- Simple reconnect screen
- Manual cache validation

Exit criteria:
- Two players can connect successfully
- Two players can play together in same world
- Manual world sync works
- Manual host switch works

## Phase 2: Automatic Migration

Features:

- Lease system
- Automatic host migration
- Automatic reconnect
- Region dirty queue
- Snapshot uploads

## Phase 3: Warm Standby

Features:

- Host score system
- Warm standby hosts
- Dynamic migration logic
- Better reconnect flow

## Phase 4: Optimization Layer

Features:

- Portal preloading
- Smarter background sync
- Better cache cleanup
- Better UI
- Better compression tuning



# Future Roadmap

Potential future upgrades:

- Fabric mod support
- Compatibility scoring for mods
- Relay fallback networking
- STUN/TURN NAT traversal
- Better chunk delta syncing
- Chunk-level storage
- More than 4 players
- Multiple permission roles
- Forge support
- Distributed chunk ownership
- Better reconnect experience
- Advanced host migration without freeze

