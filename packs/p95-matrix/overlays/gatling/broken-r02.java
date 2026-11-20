
import static io.gatling.javaapi.core.CoreDsl.*;
import static io.gatling.javaapi.http.HttpDsl.*;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.gatling.javaapi.core.*;
import io.gatling.javaapi.http.*;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class ModelSimulation extends Simulation {
    private static final String CONFIG_JSON = "{\"users\": 2, \"iterations\": 2, \"mode\": \"flow\"}";
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final JsonNode CFG = readConfig();
    private static final String MODE = CFG.path("mode").asText("flow");
    private static final HttpProtocolBuilder HTTP = http.baseUrl(System.getenv("BASE_URL"))
        .disableWarmUp().acceptHeader("application/json");

    private static JsonNode readConfig() {
        try {
            return JSON.readTree(CONFIG_JSON);
        } catch (IOException error) {
            throw new IllegalStateException(error);
        }
    }

    private static JsonNode row(Session session) {
        try {
            JsonNode rows = JSON.readTree(Files.readString(Path.of("/fixtures/accounts.json")));
            return rows.get((int) ((session.userId() - 1) % rows.size()));
        } catch (IOException | RuntimeException error) {
            return JSON.createObjectNode().put("username", "bench-" + session.userId())
                .put("password", "bench").put("quantity", 2);
        }
    }

    private static String username(Session session) {
        if (MODE.equals("ownership")) return "bench";
        if (MODE.equals("data") || MODE.equals("partition")) return row(session).path("username").asText();
        return "bench-" + ((session.userId() - 1) % 16 + 1);
    }

    private static String password(Session session) {
        return MODE.equals("data") || MODE.equals("partition")
            ? row(session).path("password").asText("bench") : "bench";
    }

    private static int quantity(Session session) {
        return MODE.equals("data") ? row(session).path("quantity").asInt(2) : CFG.path("quantity").asInt(2);
    }

    private static ChainBuilder login() {
        return exec(session -> session.set("csrf", "").set("grant", "").remove("location")
                .remove("product").remove("variant").remove("nextPage").remove("etag")
                .remove("pollUrl").remove("reservation").remove("jobState").remove("patchStatus")
                .remove("catalogStatus").remove("challengeHtml").remove("challengeAction")
                .remove("challengeToken"))
            .exec(http("login form").get("/v2/login-form")
                .check(regex("<input(?=[^>]*\\bname=[\\\"']csrf[\\\"'])(?=[^>]*\\bvalue=[\\\"']([^\\\"']+)[\\\"'])[^>]*>")
                    .saveAs("formCsrf")))
            .exec(http("login").post("/v2/login")
                .disableFollowRedirect()
                .header("Content-Type", "application/json")
                .body(StringBody(session -> "{\"username\":\"" + username(session) + "\",\"password\":\""
                    + password(session) + "\",\"csrf\":\"" + session.getString("formCsrf") + "\"}"))
                .check(status().in(200, 302), jsonPath("$.csrf").optional().saveAs("csrf"),
                    jsonPath("$.grant_id").optional().saveAs("grant"),
                    header("Location").optional().saveAs("location")))
            .exec(doIf(session -> session.contains("location")).then(
                exec(http("login redirect").get("#{location}").header("X-CSRF-Token", "#{csrf}")
                    .check(jsonPath("$.grant_id").optional().saveAs("grant"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))));
    }

    private static ChainBuilder challenge() {
        return exec(http("challenge form").get("/v2/challenge-form").header("X-CSRF-Token", "#{csrf}")
                .check(bodyString().saveAs("challengeHtml")))
            .exec(session -> {
                String html = session.getString("challengeHtml");
                Matcher action = Pattern.compile("(?i)<form[^>]*\\baction=[\\\"']([^\\\"']+)[\\\"']")
                    .matcher(html);
                Matcher token = Pattern.compile("(?i)<input(?=[^>]*\\bname=[\\\"']challenge[\\\"'])"
                    + "(?=[^>]*\\bvalue=[\\\"']([^\\\"']+)[\\\"'])[^>]*>").matcher(html);
                return action.find() && token.find()
                    ? session.set("challengeAction", action.group(1)).set("challengeToken", token.group(1))
                    : session;
            })
            .exec(http("challenge submit").post("#{challengeAction}")
                .disableFollowRedirect()
                .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                .body(StringBody(session -> "{\"challenge\":\"" + session.getString("challengeToken") + "\"}"))
                .check(status().in(200, 302), header("Location").optional().saveAs("location"),
                    jsonPath("$.grant_id").optional().saveAs("grant"),
                    jsonPath("$.csrf").optional().saveAs("csrf")))
            .exec(doIf(session -> session.contains("location")).then(
                exec(http("challenge redirect").get("#{location}").header("X-CSRF-Token", "#{csrf}")
                    .check(jsonPath("$.grant_id").optional().saveAs("grant"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))));
    }

    private static Duration retryDelay(Session session) {
        try {
            return Duration.ofMillis(Math.max(10,
                (long) (Double.parseDouble(session.getString("retryAfter")) * 1000 + 10)));
        } catch (RuntimeException error) {
            return Duration.ofMillis(20);
        }
    }

    private static ChainBuilder workRequest(String path) {
        ChainBuilder chain = exec(http("work").post(path)
            .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
            .body(StringBody("{}"))
            .check(status().in(200, 401, 429), status().saveAs("workStatus"),
                header("Retry-After").optional().saveAs("retryAfter"),
                jsonPath("$.csrf").optional().saveAs("csrf")));
        if (MODE.equals("errors")) {
            chain = chain.exec(doIf(session -> session.getInt("workStatus") == 401).then(
                exec(http("renew").post("/v2/session/renew")
                    .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                    .body(StringBody("{}"))
                    .check(status().is(200), jsonPath("$.csrf").optional().saveAs("csrf")))
                    .exec(http("work retry").post(path)
                        .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                        .body(StringBody("{}"))
                        .check(status().in(200, 401, 429), status().saveAs("workStatus"),
                            header("Retry-After").optional().saveAs("retryAfter"),
                            jsonPath("$.csrf").optional().saveAs("csrf")))));
        }
        if (CFG.path("completion").asBoolean(false)) {
            for (int retry = 0; retry < 4; retry++) {
                chain = chain.exec(doIf(session -> session.getInt("workStatus") == 429).then(
                    pause(session -> retryDelay(session)).exec(http("work retry").post(path)
                        .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                        .body(StringBody("{}"))
                        .check(status().in(200, 429), status().saveAs("workStatus"),
                            header("Retry-After").optional().saveAs("retryAfter"),
                            jsonPath("$.csrf").optional().saveAs("csrf")))));
            }
        }
        return chain;
    }

    private static ChainBuilder work(String path) {
        return login().exec(workRequest(path));
    }

    private static Session selectProduct(Session session) {
        try {
            JsonNode products = JSON.readTree(session.getString("catalogBody"))
                .path("data").path("products");
            for (JsonNode product : products) {
                for (JsonNode variant : product.path("variants")) {
                    if (variant.path("in_stock").asBoolean()) {
                        return session.set("product", product.path("id").asText())
                            .set("variant", variant.path("id").asText());
                    }
                }
            }
        } catch (IOException error) {
            return session;
        }
        return session;
    }

    private static ChainBuilder flow() {
        ChainBuilder chain = login();
        if (MODE.equals("challenge")) chain = chain.exec(challenge());
        if (MODE.equals("ownership")) {
            chain = chain.exec(doIf(session -> session.userId() != 1).then(pause(Duration.ofMillis(500))));
        }
        if (MODE.equals("expiry")) chain = chain.exec(pause(Duration.ofMillis(2100)));
        String catalogPath = MODE.equals("pagination") ? "/v2/catalog/page" : "/v2/catalog";
        chain = chain.exec(http("catalog").get(catalogPath).header("X-CSRF-Token", "#{csrf}")
            .check(status().in(200, 401), status().saveAs("catalogStatus"),
                bodyString().saveAs("catalogBody"), jsonPath("$.csrf").optional().saveAs("csrf"),
                jsonPath("$.next").optional().saveAs("nextPage")))
            .exec(doIf(session -> session.getInt("catalogStatus") == 401).then(
                exec(http("renew").post("/v2/session/renew")
                    .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                    .body(StringBody("{}"))
                    .check(status().in(200, 401), status().saveAs("renewStatus"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(doIf(session -> MODE.equals("ownership")
                    && session.getInt("renewStatus") == 401).then(login()))
                .exec(http("catalog retry").get(catalogPath).header("X-CSRF-Token", "#{csrf}")
                    .check(status().is(200), bodyString().saveAs("catalogBody"),
                        jsonPath("$.csrf").optional().saveAs("csrf"),
                        jsonPath("$.next").optional().saveAs("nextPage")))))
            .exec(session -> selectProduct(session));
        if (MODE.equals("pagination")) {
            for (int page = 0; page < 2; page++) {
                chain = chain.exec(doIf(session -> !session.contains("product")
                    && session.contains("nextPage")).then(
                    exec(http("catalog next").get("#{nextPage}").header("X-CSRF-Token", "#{csrf}")
                        .check(status().is(200), bodyString().saveAs("catalogBody"),
                            jsonPath("$.csrf").optional().saveAs("csrf"),
                            jsonPath("$.next").optional().saveAs("nextPage")))
                    .exec(session -> selectProduct(session))));
            }
        }
        chain = chain
            .exec(doIf(session -> !session.contains("cart")).then(
                exec(http("cart").post("/v2/carts")
                .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                .body(StringBody(session -> "{\"product_id\":\"" + session.getString("product")
                    + "\",\"variant_id\":\"" + session.getString("variant")
                    + "\",\"grant_id\":\"" + session.getString("grant") + "\"}"))
                .check(status().in(200, 201), jsonPath("$.id").saveAs("cart"),
                    header("ETag").optional().saveAs("etag"), jsonPath("$.csrf").optional().saveAs("csrf")))));

        if (MODE.equals("expiry") || MODE.equals("denial") || MODE.equals("ownership")) {
            chain = chain.exec(doIf(session -> MODE.equals("ownership") && session.userId() == 1)
                    .then(pause(Duration.ofMillis(300))))
                .exec(doIf(session -> MODE.equals("ownership") && session.userId() != 1)
                    .then(pause(Duration.ofMillis(500))))
                .exec(http("expiry probe").get("/v2/carts/#{cart}")
                    .header("X-CSRF-Token", "#{csrf}")
                    .check(status().in(200, 401), status().saveAs("expiryStatus")))
                .exec(doIf(session -> session.getInt("expiryStatus") == 401).then(
                    exec(http("renew").post("/v2/session/renew")
                        .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                        .body(StringBody("{}"))
                        .check(status().in(200, 401), status().saveAs("renewStatus"),
                            jsonPath("$.csrf").optional().saveAs("csrf")))
                    .exec(doIf(session -> MODE.equals("ownership")
                        && session.getInt("renewStatus") == 401).then(
                        exec(session -> session.set("recoveryCart", session.getString("cart"))
                                .set("recoveryEtag", session.getString("etag")))
                            .exec(login())
                            .exec(session -> session.set("cart", session.getString("recoveryCart"))
                                .set("etag", session.getString("recoveryEtag")))
                            .exec(http("cart recovery").post("/v2/carts/#{cart}/recover")
                                .header("X-CSRF-Token", "#{csrf}")
                                .check(status().is(200), header("ETag").optional().saveAs("etag"),
                                    jsonPath("$.csrf").optional().saveAs("csrf")))))));
        }

        if (MODE.equals("etag") || MODE.equals("data")) {
            chain = chain.exec(http("cart update").patch("/v2/carts/#{cart}")
                .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                .header("If-Match", "#{etag}")
                .body(StringBody(session -> "{\"quantity\":" + quantity(session) + "}"))
                .check(status().in(200, 412), status().saveAs("patchStatus"),
                    header("ETag").optional().saveAs("etag"), jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(doIf(session -> session.getInt("patchStatus") == 412).then(
                    exec(http("etag refresh").get("/v2/carts/#{cart}").header("X-CSRF-Token", "#{csrf}")
                        .check(header("ETag").saveAs("etag"), jsonPath("$.csrf").optional().saveAs("csrf")))
                    .exec(http("cart retry").patch("/v2/carts/#{cart}")
                        .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                        .header("If-Match", "#{etag}")
                        .body(StringBody(session -> "{\"quantity\":" + quantity(session) + "}"))
                        .check(status().is(200), header("ETag").optional().saveAs("etag"),
                            jsonPath("$.csrf").optional().saveAs("csrf")))));
        }

        if (MODE.equals("reservation")) {
            chain = chain.exec(http("reservation").post("/v2/reservations")
                    .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                    .body(StringBody(session -> "{\"cart_id\":\"" + session.getString("cart") + "\"}"))
                    .check(status().is(202), jsonPath("$.status_url").saveAs("pollUrl"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(http("reservation poll").get("#{pollUrl}").header("X-CSRF-Token", "#{csrf}")
                    .check(jsonPath("$.status").saveAs("jobState"),
                        jsonPath("$.reservation_id").optional().saveAs("reservation"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(pause(Duration.ofMillis(100)))
                .exec(http("reservation poll").get("#{pollUrl}").header("X-CSRF-Token", "#{csrf}")
                    .check(jsonPath("$.status").saveAs("jobState"),
                        jsonPath("$.reservation_id").optional().saveAs("reservation"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(pause(Duration.ofMillis(100)))
                .exec(http("reservation poll").get("#{pollUrl}").header("X-CSRF-Token", "#{csrf}")
                    .check(jsonPath("$.status").saveAs("jobState"),
                        jsonPath("$.reservation_id").optional().saveAs("reservation"),
                        jsonPath("$.csrf").optional().saveAs("csrf")))
                .exec(doIf(session -> !"ready".equals(session.getString("jobState"))).then(
                    pause(Duration.ofMillis(100))
                        .exec(http("reservation assertion").get("#{pollUrl}").header("X-CSRF-Token", "#{csrf}")
                            .check(jsonPath("$.status").is("ready")))));
        }

        return chain.exec(doIf(session -> !MODE.equals("reservation")
                || "ready".equals(session.getString("jobState"))).then(
            exec(http(MODE.equals("threshold") ? "checkout" : "commit").post("/v2/orders")
                .header("Content-Type", "application/json").header("X-CSRF-Token", "#{csrf}")
                .header("Idempotency-Key", "#{cart}").header("If-Match", "#{etag}")
                .body(StringBody(session -> "{\"cart_id\":\"" + session.getString("cart") + "\""
                    + (MODE.equals("reservation") ? ",\"reservation_id\":\""
                        + session.getString("reservation") + "\"" : "") + "}"))
                .check(status().is(201), jsonPath("$.accepted").is("true"),
                    jsonPath("$.csrf").optional().saveAs("csrf")))));
    }

    public ModelSimulation() {
        setUp(scenario("matrix").exec(repeat(2).on(flow())).injectOpen(atOnceUsers(2)))
            .assertions(MODE.equals("threshold")
                ? details("checkout").responseTime().percentile3().lt(250)
                : global().failedRequests().count().is(0L))
            .protocols(HTTP);
    }
}
