package com.serverless_mc.mixin;

import com.serverless_mc.MigrationFreezeScreen;
import com.serverless_mc.ServerlessMcMod;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ConnectScreen;
import net.minecraft.client.gui.screen.DisconnectedScreen;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.network.ServerAddress;
import net.minecraft.client.network.ServerInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(Screen.class)
public abstract class ScreenMixin {

    private static final long RECONNECT_COOLDOWN_MS = 10_000;
    private static final int MAX_RECONNECT_ATTEMPTS = 3;

    @Inject(method = "tick", at = @At("HEAD"))
    private void onTick(CallbackInfo ci) {
        boolean isDisconnected = (Object) this instanceof DisconnectedScreen;
        boolean isFreeze = (Object) this instanceof MigrationFreezeScreen;

        if (!isDisconnected && !isFreeze) {
            return;
        }

        // If state is migrating and we're on DisconnectedScreen, switch to our freeze screen
        if ("migrating".equals(ServerlessMcMod.currentSession.state)) {
            if (isDisconnected) {
                ServerlessMcMod.LOGGER.info("[SMC] Replacing DisconnectedScreen with MigrationFreezeScreen.");
                MinecraftClient.getInstance().setScreen(new MigrationFreezeScreen());
            }
            return;
        }

        // Ready to reconnect: state is "active" and we have an address
        if ("active".equals(ServerlessMcMod.currentSession.state) && ServerlessMcMod.currentSession.address != null) {
            long now = System.currentTimeMillis();
            long timeSinceLastAttempt = now - ServerlessMcMod.currentSession.lastReconnectAttempt;

            if (timeSinceLastAttempt < RECONNECT_COOLDOWN_MS) {
                return;
            }

            if (ServerlessMcMod.currentSession.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
                ServerlessMcMod.LOGGER.warn("[SMC] Max reconnect attempts ({}) reached. Giving up.", MAX_RECONNECT_ATTEMPTS);
                ServerlessMcMod.currentSession.state = "idle";
                ServerlessMcMod.currentSession.resetMigrationState();
                return;
            }

            String addr = ServerlessMcMod.currentSession.address;
            int attempt = ServerlessMcMod.currentSession.reconnectAttempts + 1;
            ServerlessMcMod.LOGGER.info("[SMC] Reconnecting to: {} (attempt {}/{})", addr, attempt, MAX_RECONNECT_ATTEMPTS);

            ServerlessMcMod.currentSession.lastReconnectAttempt = now;
            ServerlessMcMod.currentSession.reconnectAttempts++;
            ServerlessMcMod.currentSession.stableInWorld = false;
            
            MinecraftClient client = MinecraftClient.getInstance();
            ServerAddress serverAddress = ServerAddress.parse(addr);
            ServerInfo serverInfo = new ServerInfo("Serverless MC Host", addr, false);
            
            ConnectScreen.connect((Screen) (Object) this, client, serverAddress, serverInfo, false);
        }
    }
}
