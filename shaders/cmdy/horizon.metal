// Horizon — a dawn gradient whose mood shifts over three minutes.
// From cmdy's calm set. Fork freely: edit, save, the terminal restyles live.
// author: cmdy · license: MIT · marketplace id: cmdy/horizon

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


    // Horizon: a dawn whose mood shifts over three minutes.
    float hue = u.time / 180.0;
    float3 low = cmdy_palette(hue) * float3(0.5, 0.35, 0.30) * 0.5;
    float3 high = cmdy_palette(hue + 0.45) * 0.18;
    fx = mix(high, low, smoothstep(0.15, 0.95, uv.y)) * 0.7;
    float d = length(sq - float2(0.0, 0.32));
    fx += cmdy_palette(hue) * exp(-d * 9.0) * 0.10;
    rgb = mix(u.background.rgb + fx, rgb, mask);
    return float4(rgb, sceneColor.a);
}
