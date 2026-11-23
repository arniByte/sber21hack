import React, { useEffect, useRef } from "react";
import {
  Color, Mesh, OrthographicCamera, Scene, ShaderMaterial, SphereGeometry, WebGLRenderer,
} from "three";

const spheres = [
  { x: -.59, y: -.30, radius: .54, phase: 2.7, colors: ["#6b5cc6", "#aab5fd", "#e6edff"] },
  { x: .80, y: .44, radius: .56, phase: 1.7, colors: ["#d27978", "#f3b094", "#fff0bb"] },
  { x: -.02, y: .02, radius: .87, phase: .2, colors: ["#126e74", "#65beb4", "#f0edaa"] },
  { x: .97, y: -.63, radius: .28, phase: 3.8, colors: ["#875eaf", "#f0b9d9", "#fee9e1"] },
];

const vertex = `
varying vec3 surface;
varying vec3 facing;
void main() {
  surface = normal;
  facing = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const fragment = `
uniform vec3 deep;
uniform vec3 middle;
uniform vec3 light;
uniform float phase;
uniform float time;
varying vec3 surface;
varying vec3 facing;
void main() {
  vec3 n = normalize(facing);
  float swirl = sin(surface.x * 2.8 + surface.z * 2.1 + phase + time * .2);
  float height = surface.y + .20 * swirl;
  vec3 base = mix(light, middle, smoothstep(-.9, .16, height));
  base = mix(base, deep, smoothstep(-.04, .88, height));
  float illumination = dot(n, normalize(vec3(-.48, .53, .83)));
  base *= .69 + .32 * smoothstep(-.8, .88, illumination);
  float reflection = pow(max(0.0, dot(n, normalize(vec3(-.35, .57, 1.)))), 23.);
  float rim = pow(1. - max(n.z, 0.), 2.6);
  base = mix(base, vec3(.96, .98, 1.), reflection * .58 + rim * .43);
  base += .012 * (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898,78.233))) * 43758.5453) - .5);
  gl_FragColor = vec4(base, 1.0);
  #include <colorspace_fragment>
}`;

export default function OrbScene() {
  const host = useRef(null);

  useEffect(() => {
    const element = host.current;
    const motion = matchMedia("(prefers-reduced-motion: reduce)");
    // The CSS artwork stays visible if the device omits WebGL.
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2", { alpha: true, antialias: true, powerPreference: "low-power" });
    if (!context) return;
    let renderer;
    try {
      renderer = new WebGLRenderer({ canvas, context, alpha: true });
    } catch {
      return;
    }

    renderer.setClearColor(0, 0);
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.65));
    const scene = new Scene();
    const camera = new OrthographicCamera(-2, 2, 1.25, -1.25, .1, 20);
    camera.position.z = 7;
    const geometry = new SphereGeometry(1, 44, 32);
    const artwork = spheres.map((orb, index) => {
      const material = new ShaderMaterial({
        vertexShader: vertex,
        fragmentShader: fragment,
        uniforms: {
          deep: { value: new Color(orb.colors[0]) },
          middle: { value: new Color(orb.colors[1]) },
          light: { value: new Color(orb.colors[2]) },
          phase: { value: orb.phase },
          time: { value: 0 },
        },
      });
      const mesh = new Mesh(geometry, material);
      mesh.scale.setScalar(orb.radius);
      mesh.position.z = index < 2 ? -1.0 + index * .1 : 0.4 + index * .11;
      scene.add(mesh);
      return { mesh, material, orb };
    });
    canvas.className = "orb-canvas";
    element.append(canvas);

    let visible = true;
    let lost = false;
    let frame = 0;
    let last = 0;
    const since = performance.now();
    const draw = (now) => {
      if (lost) return;
      const time = motion.matches ? 0 : (now - since) / 1000;
      for (const { mesh, material, orb } of artwork) {
        mesh.position.x = orb.x + Math.sin(time * .48 + orb.phase) * .12;
        mesh.position.y = orb.y + Math.cos(time * .56 + orb.phase) * .095;
        mesh.rotation.y = Math.sin(time * .28 + orb.phase) * .36;
        mesh.rotation.z = Math.cos(time * .20 + orb.phase) * .14;
        material.uniforms.time.value = time;
      }
      renderer.render(scene, camera);
    };
    const animate = (now) => {
      frame = 0;
      if (!visible || document.hidden || motion.matches || lost) return;
      if (now - last >= 27) {
        draw(now);
        last = now;
      }
      frame = requestAnimationFrame(animate);
    };
    const refresh = () => {
      cancelAnimationFrame(frame);
      frame = 0;
      if (!visible || document.hidden || lost) return;
      draw(performance.now());
      if (!motion.matches) frame = requestAnimationFrame(animate);
    };
    const resize = new ResizeObserver(() => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      if (!width || !height) return;
      const halfHeight = Math.max(1.2, 1.35 * height / width);
      const halfWidth = halfHeight * width / height;
      Object.assign(camera, { left: -halfWidth, right: halfWidth, top: halfHeight, bottom: -halfHeight });
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      refresh();
      if (!lost) element.dataset.webgl = "ready";
    });
    const intersection = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      refresh();
    });
    const onLost = (event) => {
      event.preventDefault();
      lost = true;
      delete element.dataset.webgl;
      refresh();
    };
    const onRestored = () => {
      lost = false;
      refresh();
      element.dataset.webgl = "ready";
    };
    resize.observe(element);
    intersection.observe(element);
    motion.addEventListener("change", refresh);
    document.addEventListener("visibilitychange", refresh);
    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      intersection.disconnect();
      motion.removeEventListener("change", refresh);
      document.removeEventListener("visibilitychange", refresh);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      geometry.dispose();
      artwork.forEach(({ material }) => material.dispose());
      renderer.dispose();
      canvas.remove();
      delete element.dataset.webgl;
    };
  }, []);

  return <div ref={host} className="orb-scene" aria-hidden="true">
    <div className="orb-soft" />
    <div className="orb-fallback"><i /><i /><i /><i /></div>
  </div>;
}
