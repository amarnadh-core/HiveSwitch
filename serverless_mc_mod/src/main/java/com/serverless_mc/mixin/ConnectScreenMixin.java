package com.serverless_mc.mixin;

import com.serverless_mc.ServerlessMcMod;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ConnectScreen;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.network.ServerAddress;
import net.minecraft.client.network.ServerInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Intercepts ConnectScreen.connect() to redirect connections to the
 * current active session host. This means the "test" server entry in
 * the multiplayer list always connects to whichever host is currently
 * active (Windows OR WSL), without needing separate server entries.
 */
@Mixin(ConnectScreen.class)
public class ConnectScreenMixin {

    @Inject(method = "connect(Lnet/minecraft/client/gui/screen/Screen;Lnet/minecraft/client/MinecraftClient;Lnet/minecraft/client/network/ServerAddress;Lnet/minecraft/client/network/ServerInfo;Z)V",
            at = @At("HEAD"),
            cancellable = true)
    private static void redirectToActiveHost(Screen screen, MinecraftClient client, ServerAddress address, ServerInfo info, boolean quickPlay, CallbackInfo ci) {
        String sessionState = ServerlessMcMod.currentSession.state;
        String sessionAddress = ServerlessMcMod.currentSession.address;

        // Only redirect if we have an active session with a known address
        if (sessionAddress == null || sessionAddress.isEmpty()) {
            return;
        }

        // Parse the current session address
        ServerAddress activeAddress = ServerAddress.parse(sessionAddress);
        
        // Check if the player is trying to connect to a local-ish address
        // (the "test" entry pointing at localhost/127.0.0.1)
        String targetHost = address.getAddress().toLowerCase();
        boolean isLocalTarget = targetHost.equals("localhost") 
                || targetHost.equals("127.0.0.1")
                || targetHost.startsWith("192.168.")
                || targetHost.startsWith("172.");

        // If target is local AND the session points somewhere different, redirect
        if (isLocalTarget && !activeAddress.getAddress().equals(address.getAddress())) {
            ServerlessMcMod.LOGGER.info("[SMC] Redirecting connection from {} to active session host {}",
                    address.getAddress() + ":" + address.getPort(),
                    sessionAddress);

            // Update the ServerInfo display so it shows the right address
            if (info != null) {
                info.address = sessionAddress;
            }

            // Cancel the original call and re-invoke with the correct address
            ci.cancel();
            ConnectScreen.connect(screen, client, activeAddress, info, quickPlay);
        }
    }
}
