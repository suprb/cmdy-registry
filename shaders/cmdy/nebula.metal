// Nebula — slow-turning gas in magenta and indigo, dim stars.
// From cmdy's calm set. Fork freely: edit, save, the terminal restyles live.
// author: cmdy · license: MIT · marketplace id: cmdy/nebula

// Value noise + 3-octave fbm (the built-in helpers cover hash/palette/mask;
// organic fields bring their own noise).
static float cmdy_vnoise(float2 p) {
    float2 i = floor(p), f = fract(p);
    float2 s = f * f * (3.0 - 2.0 * f);
    float a = cmdy_hash(i);
    float b = cmdy_hash(i + float2(1.0, 0.0));
    float c = cmdy_hash(i + float2(0.0, 1.0));
    float d = cmdy_hash(i + float2(1.0, 1.0));
    return mix(mix(a, b, s.x), mix(c, d, s.x), s.y);
}
static float cmdy_fbm(float2 p) {
    float v = 0.0, amp = 0.5;
    for (int i = 0; i < 3; i++) {
        v += cmdy_vnoise(p) * amp;
        p = p * 2.03 + 17.31;
        amp *= 0.5;
    }
    return v;
}

float4 cmdy_main(float2 uv, float4 sceneColor,
                    constant CmdyUniforms &u,
                    texture2d<float> scene, sampler smp) {
    float3 rgb = sceneColor.rgb;
    float aspect = u.resolution.x / u.resolution.y;
    float2 sq = (uv - 0.5) * float2(aspect, 1.0);
    float2 pp = uv * u.resolution;
    float mask = cmdy_textMask(rgb, u.background.rgb);
    float3 fx = float3(0.0);
    (void)pp;


    // Nebula: slow-turning gas, a few dim stars.
    float ang = u.time * 0.01;
    float2 rp = float2(sq.x * cos(ang) - sq.y * sin(ang),
                       sq.x * sin(ang) + sq.y * cos(ang));
    float n = cmdy_fbm(rp * 2.2 + 3.7);
    float n2 = cmdy_fbm(rp * 4.4 - 1.3);
    fx = float3(0.13, 0.05, 0.15) * smoothstep(0.35, 0.80, n)
       + float3(0.04, 0.06, 0.15) * smoothstep(0.40, 0.85, n2) * 0.8;
    float sh = cmdy_hash(floor(pp / 3.0));
    if (sh > 0.9975) { fx += float3(0.45) * (0.4 + 0.3 * sin(u.time + sh * 99.0)); }
    rgb = mix(u.background.rgb + fx, rgb, mask);
    return float4(rgb, sceneColor.a);
}
