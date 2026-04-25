package com.serverless_mc;

import net.fabricmc.api.EnvType;
import net.fabricmc.api.Environment;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.client.gui.screen.multiplayer.MultiplayerScreen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.text.Text;

@Environment(EnvType.CLIENT)
public class MigrationFreezeScreen extends Screen {

    private long openedAtMillis;
    private static final long TIMEOUT_MS = 30_000; // 30 seconds
    /** Grace period: if player lands in-world, auto-close after this many ms */
    private static final long IN_WORLD_GRACE_MS = 2_000; // 2 seconds
    private long inWorldSinceMillis = -1;

    public MigrationFreezeScreen() {
        super(Text.literal("Host Migration"));
        this.openedAtMillis = System.currentTimeMillis();
    }

    @Override
    protected void init() {
        super.init();
        this.addDrawableChild(ButtonWidget.builder(
                Text.literal("Cancel Migration"),
                button -> cancelMigration()
        ).dimensions(this.width / 2 - 100, this.height / 2 + 40, 200, 20).build());
    }

    private void cancelMigration() {
        ServerlessMcMod.currentSession.state = "idle";
        ServerlessMcMod.currentSession.resetMigrationState();
        ServerlessMcMod.LOGGER.info("[SMC] Migration cancelled by user.");
        if (this.client != null) {
            this.client.disconnect();
            this.client.setScreen(new MultiplayerScreen(new TitleScreen()));
        }
    }

    @Override
    public boolean shouldCloseOnEsc() {
        return true;
    }

    @Override
    public void close() {
        cancelMigration();
    }

    @Override
    public boolean shouldPause() {
        return false;
    }

    @Override
    public void tick() {
        super.tick();
        long now = System.currentTimeMillis();

        // --- Hard failsafe: if player is already in-world, auto-close ---
        if (this.client != null && this.client.world != null && this.client.player != null) {
            if (inWorldSinceMillis < 0) {
                inWorldSinceMillis = now;
                ServerlessMcMod.LOGGER.info("[SMC] Player detected in-world while freeze screen is active. Starting grace period.");
            }
            long inWorldDuration = now - inWorldSinceMillis;
            if (inWorldDuration >= IN_WORLD_GRACE_MS) {
                ServerlessMcMod.LOGGER.info("[SMC] Grace period elapsed. Closing freeze screen — player confirmed in-world for {}ms.", inWorldDuration);
                ServerlessMcMod.currentSession.stableInWorld = true;
                ServerlessMcMod.currentSession.resetMigrationState();
                this.client.setScreen(null);
                return;
            }
        } else {
            inWorldSinceMillis = -1; // Reset if player leaves world
        }

        // --- Timeout failsafe ---
        if (now - openedAtMillis > TIMEOUT_MS) {
            ServerlessMcMod.LOGGER.warn("[SMC] Migration freeze timed out after 30 seconds.");
            cancelMigration();
        }
    }

    @Override
    public void render(DrawContext context, int mouseX, int mouseY, float delta) {
        context.fillGradient(0, 0, this.width, this.height, -1072689136, -804253680);

        long elapsed = System.currentTimeMillis() - openedAtMillis;
        int secondsElapsed = (int) (elapsed / 1000);
        int secondsRemaining = Math.max(0, (int) ((TIMEOUT_MS - elapsed) / 1000));

        String titleStr = "Host Migration In Progress";
        int titleWidth = this.textRenderer.getWidth(titleStr);
        context.drawTextWithShadow(this.textRenderer, titleStr, (this.width - titleWidth) / 2, this.height / 2 - 30, 0xFFFFFF);

        String subtitle = "Waiting for new host... (" + secondsElapsed + "s elapsed, " + secondsRemaining + "s until timeout)";
        int subWidth = this.textRenderer.getWidth(subtitle);
        context.drawTextWithShadow(this.textRenderer, subtitle, (this.width - subWidth) / 2, this.height / 2 - 10, 0xAAAAAA);

        String stateInfo = "State: " + ServerlessMcMod.currentSession.state + " | Target: " + ServerlessMcMod.currentSession.address;
        int stateWidth = this.textRenderer.getWidth(stateInfo);
        context.drawTextWithShadow(this.textRenderer, stateInfo, (this.width - stateWidth) / 2, this.height / 2 + 10, 0x888888);

        // Show if in-world detected
        if (inWorldSinceMillis > 0) {
            String inWorldStr = "Reconnect detected! Closing shortly...";
            int inWorldWidth = this.textRenderer.getWidth(inWorldStr);
            context.drawTextWithShadow(this.textRenderer, inWorldStr, (this.width - inWorldWidth) / 2, this.height / 2 + 24, 0x55FF55);
        }

        super.render(context, mouseX, mouseY, delta);
    }
}
