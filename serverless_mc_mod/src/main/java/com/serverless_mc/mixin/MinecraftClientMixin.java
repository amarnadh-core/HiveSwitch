package com.serverless_mc.mixin;

import com.serverless_mc.ServerlessMcMod;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.DisconnectedScreen;
import net.minecraft.client.gui.screen.Screen;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;

@Mixin(MinecraftClient.class)
public class MinecraftClientMixin {

    @ModifyVariable(method = "setScreen", at = @At("HEAD"), argsOnly = true)
    private Screen modifySetScreen(Screen screen) {
        if (screen instanceof DisconnectedScreen && "migrating".equals(ServerlessMcMod.currentSession.state)) {
            return new com.serverless_mc.MigrationFreezeScreen();
        }
        return screen;
    }
}
