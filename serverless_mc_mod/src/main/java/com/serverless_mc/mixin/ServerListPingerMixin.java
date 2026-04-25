package com.serverless_mc.mixin;

import com.serverless_mc.ServerlessMcMod;
import net.minecraft.client.network.ServerInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Intercepts the multiplayer server list pinger to redirect pings
 * for local servers to the current active session host.
 * This makes the "test" entry show as online even when WSL is hosting.
 */
@Mixin(net.minecraft.client.network.MultiplayerServerListPinger.class)
public class ServerListPingerMixin {

    @Inject(method = "add", at = @At("HEAD"))
    private void redirectPingAddress(ServerInfo entry, Runnable saver, CallbackInfo ci) {
        String sessionAddress = ServerlessMcMod.currentSession.address;
        if (sessionAddress == null || sessionAddress.isEmpty()) {
            return;
        }

        String targetHost = entry.address.toLowerCase();
        // Extract just the host part (before the colon if present)
        String host = targetHost.contains(":") ? targetHost.substring(0, targetHost.indexOf(":")) : targetHost;

        boolean isLocalTarget = host.equals("localhost")
                || host.equals("127.0.0.1");

        if (isLocalTarget && !"idle".equals(ServerlessMcMod.currentSession.state)) {
            String originalAddress = entry.address;
            entry.address = sessionAddress;
            ServerlessMcMod.LOGGER.info("[SMC] Redirecting server ping from {} to {}", originalAddress, sessionAddress);
        }
    }
}
