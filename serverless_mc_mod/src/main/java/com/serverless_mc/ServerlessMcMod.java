package com.serverless_mc;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.api.EnvType;
import net.fabricmc.api.Environment;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;

@Environment(EnvType.CLIENT)
public class ServerlessMcMod implements ClientModInitializer {
    public static final Logger LOGGER = LoggerFactory.getLogger("serverless_mc");
    public static SessionState currentSession = new SessionState("idle", "127.0.0.1:25565");

    /** All helper ports to try, in order. */
    private static final int[] HELPER_PORTS = {8765, 8766};

    /** Path to session.json on the shared filesystem (disk-based fallback). */
    private static Path sessionFilePath = null;

    public static class SessionState {
        public volatile String state;
        public volatile String address;
        public volatile long lastReconnectAttempt = 0;
        public volatile int reconnectAttempts = 0;
        public volatile boolean stableInWorld = false;
        public volatile boolean migrationFreezeShown = false;

        public SessionState(String state, String address) {
            this.state = state;
            this.address = address;
        }

        public void resetMigrationState() {
            this.reconnectAttempts = 0;
            this.lastReconnectAttempt = 0;
            this.migrationFreezeShown = false;
            if ("migrating".equals(this.state)) {
                this.state = "idle";
            }
        }
    }

    @Override
    public void onInitializeClient() {
        LOGGER.info("[SMC] Serverless MC mod initialized. Polling helper ports: 8765, 8766");
        
        // Discover session.json path for disk-based fallback
        sessionFilePath = discoverSessionFilePath();
        if (sessionFilePath != null) {
            LOGGER.info("[SMC] Disk fallback enabled: {}", sessionFilePath);
        } else {
            LOGGER.warn("[SMC] No session.json path configured. Disk fallback disabled.");
            LOGGER.warn("[SMC] Create config/serverless-mc.txt with the path to session.json");
        }

        Thread poller = new Thread(() -> {
            HttpClient client = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(2))
                    .build();

            int failureCount = 0;
            while (!Thread.currentThread().isInterrupted()) {
                // --- Check if player is already stably in-world ---
                net.minecraft.client.MinecraftClient mcClient = net.minecraft.client.MinecraftClient.getInstance();
                boolean playerInWorld = mcClient != null && mcClient.world != null && mcClient.player != null;

                if (playerInWorld && !currentSession.stableInWorld) {
                    currentSession.stableInWorld = true;
                    LOGGER.info("[SMC] Player is in-world. Marking stable. Clearing migration state.");
                    currentSession.resetMigrationState();
                    mcClient.execute(() -> {
                        if (mcClient.currentScreen instanceof MigrationFreezeScreen) {
                            LOGGER.info("[SMC] Auto-closing MigrationFreezeScreen — player is in-world.");
                            mcClient.setScreen(null);
                        }
                    });
                }

                // --- Poll ALL helper ports via HTTP ---
                boolean gotResponse = false;
                for (int port : HELPER_PORTS) {
                    try {
                        HttpRequest request = HttpRequest.newBuilder()
                                .uri(URI.create("http://127.0.0.1:" + port + "/session"))
                                .timeout(Duration.ofSeconds(2))
                                .GET()
                                .build();

                        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
                        if (response.statusCode() == 200) {
                            if (failureCount > 0) {
                                LOGGER.info("[SMC] Helper reachable on port {} (was unreachable for {} polls)", port, failureCount);
                            }
                            failureCount = 0;
                            gotResponse = true;
                            processSessionJson(response.body(), "HTTP:" + port);
                            break;
                        }
                    } catch (Exception e) {
                        // This port is unreachable, try the next one
                    }
                }

                // --- Disk-based fallback when no helper API responds ---
                if (!gotResponse && sessionFilePath != null) {
                    try {
                        if (Files.exists(sessionFilePath)) {
                            String fileContent = Files.readString(sessionFilePath);
                            processSessionJson(fileContent, "DISK");
                            gotResponse = true;
                            if (failureCount > 0 && failureCount % 5 == 0) {
                                LOGGER.info("[SMC] Using disk fallback (session.json). HTTP helpers unreachable for {} polls.", failureCount);
                            }
                        }
                    } catch (IOException e) {
                        // File read failed, treat as failure
                    }
                }

                if (!gotResponse) {
                    failureCount++;
                    if (failureCount >= 5 && !currentSession.stableInWorld) {
                        if (!"migrating".equals(currentSession.state)) {
                            LOGGER.info("[SMC] All sources unreachable ({} failures). Triggering migration freeze.", failureCount);
                        }
                        currentSession.state = "migrating";
                        showFreezeScreen();
                    }
                } else {
                    // Got data from some source (HTTP or disk)
                    // Only increment failure count for HTTP specifically
                    // (disk success means data is available)
                }

                try {
                    Thread.sleep(1000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
        });
        poller.setDaemon(true);
        poller.start();
    }

    /**
     * Process a session JSON string from any source (HTTP response or disk file).
     */
    private void processSessionJson(String body, String source) {
        String stateStr = extractJsonValue(body, "state");
        String addressStr = extractJsonValue(body, "server_address");

        if (stateStr != null) {
            String oldState = currentSession.state;
            currentSession.state = stateStr;

            if (!stateStr.equals(oldState)) {
                LOGGER.info("[SMC] Session state changed: {} -> {} (from {})", oldState, stateStr, source);
            }

            if ("migrating".equals(stateStr) && !currentSession.stableInWorld) {
                showFreezeScreen();
            }

            if ("active".equals(stateStr) && currentSession.stableInWorld) {
                currentSession.resetMigrationState();
            }
        }
        if (addressStr != null) {
            String oldAddr = currentSession.address;
            currentSession.address = addressStr;
            if (!addressStr.equals(oldAddr)) {
                LOGGER.info("[SMC] Server address changed: {} -> {} (from {})", oldAddr, addressStr, source);
            }
        }
    }

    /**
     * Discover the path to session.json on the shared filesystem.
     * Checks (in order):
     *   1. System property "serverless.mc.session"
     *   2. A config file at <gameDir>/config/serverless-mc.txt containing the path
     *   3. Search upward from game directory for .local-cloud/worlds/.../session.json
     */
    private Path discoverSessionFilePath() {
        // 1. System property
        String prop = System.getProperty("serverless.mc.session");
        if (prop != null && !prop.isEmpty()) {
            Path p = Path.of(prop);
            LOGGER.info("[SMC] Session path from system property: {}", p);
            return p;
        }

        // 2. Config file in Fabric config directory
        Path configDir = FabricLoader.getInstance().getConfigDir();
        Path configFile = configDir.resolve("serverless-mc.txt");
        if (Files.exists(configFile)) {
            try {
                String content = Files.readString(configFile).trim();
                if (!content.isEmpty()) {
                    Path p = Path.of(content);
                    LOGGER.info("[SMC] Session path from config file: {}", p);
                    return p;
                }
            } catch (IOException e) {
                LOGGER.warn("[SMC] Could not read config file: {}", e.getMessage());
            }
        }

        // 3. Search upward from game directory for .local-cloud
        Path gameDir = FabricLoader.getInstance().getGameDir().toAbsolutePath();
        Path dir = gameDir;
        for (int i = 0; i < 6; i++) {
            // Try common world IDs
            for (String worldId : new String[]{"prototype-world"}) {
                Path candidate = dir.resolve(".local-cloud").resolve("worlds").resolve(worldId).resolve("session.json");
                if (Files.exists(candidate)) {
                    LOGGER.info("[SMC] Session path auto-detected: {}", candidate);
                    return candidate;
                }
            }
            dir = dir.getParent();
            if (dir == null) break;
        }

        // 4. Auto-create config file with instructions
        try {
            Files.createDirectories(configDir);
            Files.writeString(configFile,
                "# Paste the full path to your session.json file here.\n" +
                "# Example: D:\\minecrafrserver\\.local-cloud\\worlds\\prototype-world\\session.json\n" +
                "# This is used as a fallback when the helper HTTP API is unreachable.\n"
            );
            LOGGER.info("[SMC] Created config template at: {}", configFile);
        } catch (IOException e) {
            // Non-fatal
        }

        return null;
    }

    private void showFreezeScreen() {
        net.minecraft.client.MinecraftClient mcClient = net.minecraft.client.MinecraftClient.getInstance();
        if (mcClient == null) return;
        if (mcClient.world != null && mcClient.player != null) return;

        mcClient.execute(() -> {
            if (!(mcClient.currentScreen instanceof MigrationFreezeScreen)) {
                LOGGER.info("[SMC] Opening MigrationFreezeScreen.");
                currentSession.migrationFreezeShown = true;
                mcClient.setScreen(new MigrationFreezeScreen());
            }
        });
    }

    static String extractJsonValue(String json, String key) {
        String search = "\"" + key + "\": \"";
        int start = json.indexOf(search);
        if (start == -1) return null;
        start += search.length();
        int end = json.indexOf("\"", start);
        if (end == -1) return null;
        return json.substring(start, end);
    }
}
