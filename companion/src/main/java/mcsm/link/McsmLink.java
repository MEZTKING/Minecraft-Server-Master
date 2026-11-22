package mcsm.link;

import com.mojang.blaze3d.platform.NativeImage;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.phys.Vec3;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Companion of the Minecraft Server Master video editor.
 *
 * <p>Runs a tiny HTTP server on 127.0.0.1 (first free port from 8796) inside the game. The port and a
 * random token are written to {@code config/mcsm-link.json}; every request must carry the token in
 * {@code X-MCSM-Token}, so only the app on this computer (which reads that file) can use it.
 *
 * <ul>
 *   <li>{@code GET /status}: is a Flashback replay open, its id, name, current tick, length, export range.</li>
 *   <li>{@code POST /view} {@code {"tick": n, "x", "y", "z", "yaw", "pitch", "fov", "roll"}}: pause the
 *       replay, go to that tick and put the camera there (like Flashback's own camera keyframes do).</li>
 *   <li>{@code GET /frame?width=960}: the picture the game draws right now, as PNG.</li>
 * </ul>
 *
 * <p>Flashback has no API for other mods, so it is used through reflection with the names of its own
 * code (checked against Flashback 0.40 for Minecraft 26.1 and the current source); if a name changes,
 * the request fails with a message instead of the game crashing.
 */
public class McsmLink implements ClientModInitializer {
    static final String VERSION = "0.1.0";
    static String token;
    static int port = -1;

    @Override
    public void onInitializeClient() {
        byte[] b = new byte[24];
        new SecureRandom().nextBytes(b);
        token = HexFormat.of().formatHex(b);
        Thread t = new Thread(McsmLink::serve, "mcsm-link");
        t.setDaemon(true);
        t.start();
    }

    // --- server ---------------------------------------------------------------------------------------
    private static void serve() {
        ServerSocket server = null;
        for (int p = 8796; p < 8816 && server == null; p++) {
            try {
                server = new ServerSocket(p, 8, InetAddress.getByName("127.0.0.1"));
                port = p;
            } catch (IOException ignored) {
            }
        }
        if (server == null) {
            System.err.println("[mcsm-link] no free port between 8796 and 8815");
            return;
        }
        writeLinkFile();
        System.out.println("[mcsm-link] listening on 127.0.0.1:" + port);
        while (true) {
            try {
                Socket s = server.accept();
                Thread h = new Thread(() -> handle(s), "mcsm-link-request");
                h.setDaemon(true);
                h.start();
            } catch (IOException e) {
                System.err.println("[mcsm-link] " + e);
            }
        }
    }

    private static void writeLinkFile() {
        try {
            Path cfg = FabricLoader.getInstance().getConfigDir().resolve("mcsm-link.json");
            Files.createDirectories(cfg.getParent());
            String json = "{\"port\": " + port + ", \"token\": \"" + token + "\", \"version\": \"" + VERSION
                    + "\", \"pid\": " + ProcessHandle.current().pid() + "}";
            Files.writeString(cfg, json, StandardCharsets.UTF_8);
        } catch (IOException e) {
            System.err.println("[mcsm-link] couldn't write config/mcsm-link.json: " + e);
        }
    }

    private static void handle(Socket s) {
        try (s) {
            s.setSoTimeout(15000);
            InputStream in = new BufferedInputStream(s.getInputStream());
            String requestLine = readLine(in);
            if (requestLine == null) return;
            Map<String, String> headers = new HashMap<>();
            for (String line; (line = readLine(in)) != null && !line.isEmpty(); ) {
                int i = line.indexOf(':');
                if (i > 0) headers.put(line.substring(0, i).trim().toLowerCase(Locale.ROOT), line.substring(i + 1).trim());
            }
            int len = Integer.parseInt(headers.getOrDefault("content-length", "0"));
            byte[] body = in.readNBytes(Math.min(len, 1 << 20));
            String[] parts = requestLine.split(" ");
            String method = parts[0], target = parts.length > 1 ? parts[1] : "/";
            OutputStream out = s.getOutputStream();
            String host = headers.getOrDefault("host", "");
            if (!token.equals(headers.get("x-mcsm-token"))
                    || !(host.equals("127.0.0.1:" + port) || host.equals("localhost:" + port))) {
                send(out, 403, "text/plain", "forbidden".getBytes(StandardCharsets.UTF_8));
                return;
            }
            String path = target.contains("?") ? target.substring(0, target.indexOf('?')) : target;
            String query = target.contains("?") ? target.substring(target.indexOf('?') + 1) : "";
            try {
                if (method.equals("GET") && path.equals("/status")) {
                    send(out, 200, "application/json", onClient(McsmLink::status).getBytes(StandardCharsets.UTF_8));
                } else if (method.equals("POST") && path.equals("/view")) {
                    String json = new String(body, StandardCharsets.UTF_8);
                    send(out, 200, "application/json", onClient(() -> view(json)).getBytes(StandardCharsets.UTF_8));
                } else if (method.equals("GET") && path.equals("/frame")) {
                    send(out, 200, "image/png", frame(intParam(query, "width", 960)));
                } else {
                    send(out, 404, "text/plain", "not found".getBytes(StandardCharsets.UTF_8));
                }
            } catch (Exception e) {
                Throwable c = e.getCause() != null ? e.getCause() : e;
                String msg = "{\"error\": " + quote(c.getClass().getSimpleName() + ": " + c.getMessage()) + "}";
                send(out, 500, "application/json", msg.getBytes(StandardCharsets.UTF_8));
            }
        } catch (IOException | RuntimeException ignored) {
        }
    }

    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') b.write(c);
            if (b.size() > 8192) throw new IOException("line too long");
        }
        if (c == -1 && b.size() == 0) return null;
        return b.toString(StandardCharsets.UTF_8);
    }

    private static void send(OutputStream out, int code, String type, byte[] body) throws IOException {
        String head = "HTTP/1.1 " + code + " " + (code == 200 ? "OK" : "Error") + "\r\nContent-Type: " + type
                + "\r\nContent-Length: " + body.length + "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n";
        out.write(head.getBytes(StandardCharsets.UTF_8));
        out.write(body);
        out.flush();
    }

    private static <T> T onClient(java.util.function.Supplier<T> job) throws Exception {
        return Minecraft.getInstance().submit(job).get(10, TimeUnit.SECONDS);
    }

    // --- Flashback through reflection --------------------------------------------------------------------
    private static Object replayServer() throws ReflectiveOperationException {
        Class<?> fb = Class.forName("com.moulberry.flashback.Flashback");
        return fb.getMethod("getReplayServer").invoke(null);
    }

    private static Object editorState() throws ReflectiveOperationException {
        Class<?> m = Class.forName("com.moulberry.flashback.state.EditorStateManager");
        return m.getMethod("getCurrent").invoke(null);
    }

    private static String status() {
        StringBuilder sb = new StringBuilder("{\"mod\": \"" + VERSION + "\", \"minecraft\": "
                + quote(SharedConstants.getCurrentVersion().name()));
        sb.append(", \"flashback\": ").append(quote(FabricLoader.getInstance().getModContainer("flashback")
                .map(c -> c.getMetadata().getVersion().getFriendlyString()).orElse(null)));
        try {
            Object rs = replayServer();
            if (rs == null) return sb.append(", \"replay\": null}").toString();
            Object meta = rs.getClass().getMethod("getMetadata").invoke(rs);
            Object id = meta.getClass().getField("replayIdentifier").get(meta);
            Object name = meta.getClass().getField("name").get(meta);
            int tick = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
            int total = (int) rs.getClass().getMethod("getTotalReplayTicks").invoke(rs);
            boolean paused = rs.getClass().getField("replayPaused").getBoolean(rs);
            sb.append(", \"replay\": {\"uuid\": ").append(quote(String.valueOf(id))).append(", \"name\": ")
                    .append(quote(name == null ? null : name.toString())).append(", \"tick\": ").append(tick)
                    .append(", \"total\": ").append(total).append(", \"paused\": ").append(paused);
            Object es = editorState();
            if (es != null) {
                Object se = es.getClass().getMethod("getExportStartAndEnd").invoke(es);
                int a = (int) se.getClass().getMethod("start").invoke(se);
                int b = (int) se.getClass().getMethod("end").invoke(se);
                sb.append(", \"export\": [").append(a).append(", ").append(b).append("]");
            }
            sb.append("}");
        } catch (ReflectiveOperationException | LinkageError e) {
            sb.append(", \"replay\": null, \"error\": ").append(quote("Flashback not found or changed: " + e));
        }
        return sb.append("}").toString();
    }

    private static final Pattern NUM = Pattern.compile("\"(\\w+)\"\\s*:\\s*(-?[0-9.eE+-]+|true|false|null)");

    private static String view(String json) {
        Map<String, String> v = new HashMap<>();
        Matcher m = NUM.matcher(json);
        while (m.find()) v.put(m.group(1), m.group(2));
        Minecraft mc = Minecraft.getInstance();
        try {
            Object rs = replayServer();
            if (rs == null) return "{\"error\": \"no Flashback replay is open\"}";
            rs.getClass().getField("replayPaused").setBoolean(rs, !"true".equals(v.get("play")));
            if (v.containsKey("tick") && !"null".equals(v.get("tick"))) {
                int tick = (int) Math.round(Double.parseDouble(v.get("tick")));
                int now = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
                if (tick != now) rs.getClass().getMethod("goToReplayTick", int.class).invoke(rs, tick);
            }
            LocalPlayer player = mc.player;
            if (player != null && v.containsKey("x")) {
                if (mc.getCameraEntity() != player && mc.getConnection() != null) mc.getConnection().sendCommand("spectate");
                player.snapTo(d(v, "x"), d(v, "y"), d(v, "z"), (float) d(v, "yaw"), (float) d(v, "pitch"));
                player.getInterpolation().cancel();
                player.setDeltaMovement(Vec3.ZERO);
            }
            Object es = editorState();
            if (es != null) {
                Object visuals = es.getClass().getField("replayVisuals").get(es);
                if (v.containsKey("fov")) visuals.getClass().getMethod("setFov", float.class).invoke(visuals, (float) d(v, "fov"));
                if (v.containsKey("roll")) {
                    float roll = (float) d(v, "roll");
                    Field on = visuals.getClass().getField("overrideRoll");
                    Field amount = visuals.getClass().getField("overrideRollAmount");
                    on.setBoolean(visuals, Math.abs(roll) > 0.01f);
                    amount.setFloat(visuals, roll);
                }
            }
            return "{\"ok\": true}";
        } catch (ReflectiveOperationException | LinkageError e) {
            return "{\"error\": " + quote("Flashback not found or changed: " + e) + "}";
        }
    }

    private static byte[] frame(int width) throws Exception {
        CompletableFuture<byte[]> done = new CompletableFuture<>();
        Minecraft mc = Minecraft.getInstance();
        mc.execute(() -> {
            try {
                var target = mc.getMainRenderTarget();
                int down = Math.max(1, Math.round(target.width / (float) Math.max(160, width)));
                Screenshot.takeScreenshot(target, down, (NativeImage img) -> {
                    try (img) {
                        Path tmp = Files.createTempFile("mcsm-link-", ".png");
                        try {
                            img.writeToFile(tmp);
                            done.complete(Files.readAllBytes(tmp));
                        } finally {
                            Files.deleteIfExists(tmp);
                        }
                    } catch (Throwable e) {
                        done.completeExceptionally(e);
                    }
                });
            } catch (Throwable e) {
                done.completeExceptionally(e);
            }
        });
        return done.get(10, TimeUnit.SECONDS);
    }

    // --- small helpers ---------------------------------------------------------------------------------
    private static double d(Map<String, String> v, String k) {
        String s = v.get(k);
        return s == null || s.equals("null") ? 0.0 : Double.parseDouble(s);
    }

    private static int intParam(String query, String key, int def) {
        for (String kv : query.split("&")) {
            String[] p = kv.split("=", 2);
            if (p.length == 2 && p[0].equals(key)) {
                try {
                    return Integer.parseInt(p[1]);
                } catch (NumberFormatException e) {
                    return def;
                }
            }
        }
        return def;
    }

    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
}
