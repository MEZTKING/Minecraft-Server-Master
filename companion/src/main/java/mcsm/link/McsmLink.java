package mcsm.link;

import com.mojang.blaze3d.platform.NativeImage;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.SharedConstants;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.world.phys.Vec3;
import org.lwjgl.stb.STBIWriteCallback;
import org.lwjgl.stb.STBImageWrite;
import org.lwjgl.system.MemoryUtil;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
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
 *   <li>{@code POST /ticket}: a one-time ticket for {@code GET /stream?k=ticket&w=960&fps=30}, a live
 *       MJPEG stream (multipart/x-mixed-replace) the editor page shows directly. Frames are captured on
 *       the game side every 1/fps s and encoded as JPEG off the render thread; only the newest is sent.</li>
 *   <li>{@code POST /path} {@code {"p": [tick, x, y, z, yaw, pitch, fov, ...]}}: the editor's camera path;
 *       {@code POST /play {"tick"}} lets Flashback play the replay from there while the mod puts the
 *       camera on the path every frame; {@code POST /pause}; {@code GET /clock}: the replay tick now.</li>
 * </ul>
 *
 * <p>Flashback has no API for other mods, so it is used through reflection with the names of its own
 * code (checked against Flashback 0.40 for Minecraft 26.1 and the current source); if a name changes,
 * the request fails with a message instead of the game crashing.
 */
public class McsmLink implements ClientModInitializer {
    static final String VERSION = "0.2.0";
    static String token;
    static int port = -1;
    static final SecureRandom RANDOM = new SecureRandom();

    // live stream: newest frame, who's watching, one capture in flight at a time
    static final Object FRAME = new Object();
    static volatile byte[] latestJpg;
    static volatile int latestTick;
    static volatile long latestSeq;
    static final AtomicInteger streamers = new AtomicInteger();
    static final AtomicBoolean capturing = new AtomicBoolean();
    static volatile long captureStarted, lastCapture, lastNudge;
    static volatile int streamWidth = 960, streamFps = 30;
    static final Map<String, Long> tickets = new ConcurrentHashMap<>();
    // following the editor's camera path while Flashback plays
    static volatile double[] path;
    static volatile boolean following;
    static final AtomicBoolean followPending = new AtomicBoolean();

    static final ScheduledExecutorService PUMP = Executors.newSingleThreadScheduledExecutor(r -> daemon(r, "mcsm-link-pump"));
    static final ExecutorService ENCODER = Executors.newSingleThreadExecutor(r -> daemon(r, "mcsm-link-jpeg"));

    private static Thread daemon(Runnable r, String name) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        return t;
    }

    @Override
    public void onInitializeClient() {
        byte[] b = new byte[24];
        RANDOM.nextBytes(b);
        token = HexFormat.of().formatHex(b);
        daemon(McsmLink::serve, "mcsm-link").start();
        PUMP.scheduleAtFixedRate(McsmLink::pump, 50, 4, TimeUnit.MILLISECONDS);
    }

    /** Every few ms: move the camera along the path (once per game frame) and take stream frames. */
    private static void pump() {
        try {
            Minecraft mc = Minecraft.getInstance();
            if (following && path != null && followPending.compareAndSet(false, true)) {
                mc.execute(() -> {
                    try {
                        followPath();
                    } catch (Throwable ignored) {
                    } finally {
                        followPending.set(false);
                    }
                });
            }
            long now = System.currentTimeMillis();
            if (streamers.get() > 0) {
                if (now - lastNudge > 1000) {             // keep the game from slowing down while you're in the editor
                    lastNudge = now;
                    mc.execute(() -> mc.getFramerateLimitTracker().onInputReceived());
                }
                if (capturing.get() && now - captureStarted > 3000) capturing.set(false);   // a lost readback
                if (now - lastCapture >= 1000 / Math.max(1, streamFps) && capturing.compareAndSet(false, true)) {
                    lastCapture = now;
                    captureStarted = now;
                    mc.execute(McsmLink::capture);
                }
            }
        } catch (Throwable ignored) {
        }
    }

    private static void capture() {
        Minecraft mc = Minecraft.getInstance();
        int tick = currentTick();
        try {
            var target = mc.getMainRenderTarget();
            int down = Math.max(1, Math.round(target.width / (float) Math.max(160, streamWidth)));
            Screenshot.takeScreenshot(target, down, (NativeImage img) -> {
                ByteBuffer copy = null;
                int w = img.getWidth(), h = img.getHeight(), comp = img.format().components();
                try (img) {
                    copy = MemoryUtil.memAlloc(w * h * comp);
                    MemoryUtil.memCopy(img.getPointer(), MemoryUtil.memAddress(copy), (long) w * h * comp);
                } catch (Throwable e) {
                    if (copy != null) MemoryUtil.memFree(copy);
                    capturing.set(false);
                    return;
                }
                ByteBuffer pixels = copy;
                ENCODER.execute(() -> {
                    try {
                        byte[] jpg = jpeg(pixels, w, h, comp, 80);
                        synchronized (FRAME) {
                            latestJpg = jpg;
                            latestTick = tick;
                            latestSeq++;
                            FRAME.notifyAll();
                        }
                    } catch (Throwable ignored) {
                    } finally {
                        MemoryUtil.memFree(pixels);
                        capturing.set(false);
                    }
                });
            });
        } catch (Throwable e) {
            capturing.set(false);
        }
    }

    private static byte[] jpeg(ByteBuffer pixels, int w, int h, int comp, int quality) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(w * h / 4);
        STBIWriteCallback cb = STBIWriteCallback.create((ctx, data, size) -> {
            ByteBuffer b = STBIWriteCallback.getData(data, size);
            byte[] a = new byte[size];
            b.get(a);
            out.write(a, 0, size);
        });
        try {
            STBImageWrite.stbi_write_jpg_to_func(cb, 0L, w, h, comp, pixels, quality);
        } finally {
            cb.free();
        }
        return out.toByteArray();
    }

    private static int currentTick() {
        try {
            Object rs = replayServer();
            return rs == null ? -1 : (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
        } catch (Throwable e) {
            return -1;
        }
    }

    /** Put the camera where the editor's path has it at the replay's current (partial) tick. */
    private static void followPath() throws ReflectiveOperationException {
        double[] p = path;
        Object rs = replayServer();
        if (p == null || p.length < 7 || rs == null) return;
        double t;
        try {
            t = (double) rs.getClass().getMethod("getPartialReplayTick").invoke(rs);
        } catch (NoSuchMethodException e) {
            t = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
        }
        int n = p.length / 7, lo = 0, hi = n - 1;
        if (t <= p[0]) hi = 0;
        else if (t >= p[(n - 1) * 7]) lo = n - 1;
        else {
            while (hi - lo > 1) {
                int mid = (lo + hi) >>> 1;
                if (p[mid * 7] <= t) lo = mid; else hi = mid;
            }
        }
        int a = lo * 7, b = hi * 7;
        double k = hi == lo ? 0 : (t - p[a]) / Math.max(1e-6, p[b] - p[a]);
        double dyaw = ((p[b + 4] - p[a + 4] + 540) % 360) - 180;
        setCamera(p[a + 1] + (p[b + 1] - p[a + 1]) * k, p[a + 2] + (p[b + 2] - p[a + 2]) * k,
                p[a + 3] + (p[b + 3] - p[a + 3]) * k, p[a + 4] + dyaw * k, p[a + 5] + (p[b + 5] - p[a + 5]) * k,
                p[a + 6] + (p[b + 6] - p[a + 6]) * k, Double.NaN);
    }

    /** Like Flashback's camera keyframes: the spectator player there, no smoothing from the last frame. */
    private static void setCamera(double x, double y, double z, double yaw, double pitch, double fov, double roll)
            throws ReflectiveOperationException {
        Minecraft mc = Minecraft.getInstance();
        LocalPlayer player = mc.player;
        if (player != null) {
            if (mc.getCameraEntity() != player && mc.getConnection() != null) mc.getConnection().sendCommand("spectate");
            player.snapTo(x, y, z, (float) yaw, (float) pitch);
            player.getInterpolation().cancel();
            player.setDeltaMovement(Vec3.ZERO);
            player.xo = player.xOld = x;
            player.yo = player.yOld = y;
            player.zo = player.zOld = z;
            player.yRotO = (float) yaw;
            player.xRotO = (float) pitch;
        }
        Object es = editorState();
        if (es != null) {
            Object visuals = es.getClass().getField("replayVisuals").get(es);
            if (!Double.isNaN(fov)) visuals.getClass().getMethod("setFov", float.class).invoke(visuals, (float) fov);
            if (!Double.isNaN(roll)) {
                visuals.getClass().getField("overrideRoll").setBoolean(visuals, Math.abs(roll) > 0.01);
                visuals.getClass().getField("overrideRollAmount").setFloat(visuals, (float) roll);
            }
        }
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
            boolean hostOk = host.equals("127.0.0.1:" + port) || host.equals("localhost:" + port);
            if (method.equals("GET") && target.startsWith("/stream?") && hostOk) {
                stream(s, out, target.substring(target.indexOf('?') + 1));
                return;
            }
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
                } else if (method.equals("POST") && path.equals("/ticket")) {
                    byte[] t = new byte[16];
                    RANDOM.nextBytes(t);
                    String k = HexFormat.of().formatHex(t);
                    tickets.put(k, System.currentTimeMillis());
                    send(out, 200, "application/json", ("{\"ticket\": \"" + k + "\"}").getBytes(StandardCharsets.UTF_8));
                } else if (method.equals("GET") && path.equals("/clock")) {
                    send(out, 200, "application/json", clock().getBytes(StandardCharsets.UTF_8));
                } else if (method.equals("POST") && path.equals("/path")) {
                    McsmLink.path = numbers(new String(body, StandardCharsets.UTF_8));
                    send(out, 200, "application/json", ("{\"ok\": true, \"points\": " + McsmLink.path.length / 7 + "}")
                            .getBytes(StandardCharsets.UTF_8));
                } else if (method.equals("POST") && (path.equals("/play") || path.equals("/pause"))) {
                    boolean play = path.equals("/play");
                    String json = new String(body, StandardCharsets.UTF_8);
                    send(out, 200, "application/json", onClient(() -> play(play, json)).getBytes(StandardCharsets.UTF_8));
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

    /** The live picture: one JPEG after another on this connection, until it closes. */
    private static void stream(Socket s, OutputStream out, String query) throws IOException {
        String k = "";
        for (String kv : query.split("&")) if (kv.startsWith("k=")) k = kv.substring(2);
        Long made = tickets.remove(k);
        if (made == null || System.currentTimeMillis() - made > 60000 || streamers.get() >= 3) {
            send(out, 403, "text/plain", "forbidden".getBytes(StandardCharsets.UTF_8));
            return;
        }
        streamWidth = Math.max(160, Math.min(3840, intParam(query, "w", 960)));
        streamFps = Math.max(1, Math.min(60, intParam(query, "fps", 30)));
        out.write(("HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=mcsmframe\r\n"
                + "Cache-Control: no-store\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
        s.setSoTimeout(0);
        streamers.incrementAndGet();
        try {
            long seen = -1;
            while (true) {
                byte[] jpg;
                int tick;
                synchronized (FRAME) {
                    long waited = 0;
                    while (latestSeq == seen && waited < 2000) {
                        try {
                            FRAME.wait(250);
                        } catch (InterruptedException e) {
                            return;
                        }
                        waited += 250;
                    }
                    if (latestSeq == seen || latestJpg == null) continue;
                    seen = latestSeq;
                    jpg = latestJpg;
                    tick = latestTick;
                }
                out.write(("--mcsmframe\r\nContent-Type: image/jpeg\r\nContent-Length: " + jpg.length + "\r\nX-Tick: "
                        + tick + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                out.write(jpg);
                out.write("\r\n".getBytes(StandardCharsets.UTF_8));
                out.flush();
            }
        } finally {
            streamers.decrementAndGet();
        }
    }

    private static String clock() {
        try {
            Object rs = replayServer();
            if (rs == null) return "{\"tick\": null}";
            int tick = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
            boolean paused = rs.getClass().getField("replayPaused").getBoolean(rs);
            return "{\"tick\": " + tick + ", \"paused\": " + paused + ", \"following\": " + following
                    + ", \"fps\": " + Minecraft.getInstance().getFps() + ", \"frames\": " + latestSeq + "}";
        } catch (ReflectiveOperationException | LinkageError e) {
            return "{\"tick\": null, \"error\": " + quote(String.valueOf(e)) + "}";
        }
    }

    private static String play(boolean play, String json) {
        try {
            Object rs = replayServer();
            if (rs == null) return "{\"error\": \"no Flashback replay is open\"}";
            if (play) {
                Matcher m = NUM.matcher(json);
                while (m.find()) {
                    if (m.group(1).equals("tick") && !m.group(2).equals("null")) {
                        int tick = (int) Math.round(Double.parseDouble(m.group(2)));
                        int now = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
                        if (tick != now) rs.getClass().getMethod("goToReplayTick", int.class).invoke(rs, tick);
                    }
                }
                following = path != null;
            } else {
                following = false;
            }
            rs.getClass().getField("replayPaused").setBoolean(rs, !play);
            return "{\"ok\": true}";
        } catch (ReflectiveOperationException | LinkageError e) {
            return "{\"error\": " + quote("Flashback not found or changed: " + e) + "}";
        }
    }

    private static double[] numbers(String json) {
        int a = json.indexOf('['), b = json.lastIndexOf(']');
        if (a < 0 || b <= a) return null;
        String[] parts = json.substring(a + 1, b).split(",");
        int n = parts.length - parts.length % 7;
        double[] out = new double[n];
        for (int i = 0; i < n; i++) out[i] = Double.parseDouble(parts[i].trim());
        return n >= 7 ? out : null;
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
        try {
            Object rs = replayServer();
            if (rs == null) return "{\"error\": \"no Flashback replay is open\"}";
            following = false;
            rs.getClass().getField("replayPaused").setBoolean(rs, !"true".equals(v.get("play")));
            if (v.containsKey("tick") && !"null".equals(v.get("tick"))) {
                int tick = (int) Math.round(Double.parseDouble(v.get("tick")));
                int now = (int) rs.getClass().getMethod("getReplayTick").invoke(rs);
                if (tick != now) rs.getClass().getMethod("goToReplayTick", int.class).invoke(rs, tick);
            }
            if (v.containsKey("x")) {
                setCamera(d(v, "x"), d(v, "y"), d(v, "z"), d(v, "yaw"), d(v, "pitch"),
                        v.containsKey("fov") ? d(v, "fov") : Double.NaN, v.containsKey("roll") ? d(v, "roll") : Double.NaN);
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
