// Slowscan — a luminous band sweeps down every twelve seconds.
// From cmdy's calm set. Fork freely: edit, save, the terminal restyles live.
// author: cmdy · license: MIT · marketplace id: cmdy/slowscan

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


    // Slowscan: a luminous band sweeps down every twelve seconds.
    float y = fract(u.time / 12.0) * 1.3 - 0.15;
    float d = uv.y - y;
    float lead = exp(-abs(d) * 60.0);
    float trail = exp(-max(-d, 0.0) * 9.0) * 0.5;
    fx = float3(0.07, 0.10, 0.09) * (lead + trail * 0.5);
    rgb = mix(u.background.rgb + fx, rgb, mask);
    return float4(rgb, sceneColor.a);
}
