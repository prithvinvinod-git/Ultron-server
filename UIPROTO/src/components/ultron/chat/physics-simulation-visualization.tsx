/**
 * Physics Simulation Visualization Component
 *
 * Renders particles with positions, velocities, and optional force fields.
 */

"use client";

import { useRef, useState, useEffect } from "react";
import { cn } from "@/lib/utils";

/** Particle data */
export interface PhysicsParticle {
  id: string;
  position: { x: number; y: number };
  velocity: { x: number; y: number };
  mass: number;
}

/** Physics simulation data */
export interface PhysicsSimulationData {
  particles: PhysicsParticle[];
  fields?: Record<string, unknown>;
}

/** Physics visualization props */
export interface PhysicsSimulationVisualizationProps {
  data: PhysicsSimulationData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Physics Visualization Component */
export function PhysicsSimulationVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: PhysicsSimulationVisualizationProps) {
  const [particles, setParticles] = useState<PhysicsParticle[]>([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setParticles(data.particles || []);
    setMounted(true);
  }, [data]);

  if (!mounted) {
    return <div className="physics-placeholder">Loading physics simulation...</div>;
  }

  // Get particle positions
  const particleNodes = particles.map(p => ({
    id: p.id,
    x: p.position.x,
    y: p.position.y,
    vx: p.velocity.x,
    vy: p.velocity.y,
    mass: p.mass,
  }));

  return (
    <div className="physics-visualization" aria-label="Physics simulation">
      <div className="physics-header">
        <span className="physics-title">Physics Simulation</span>
      </div>

      <div className="physics-canvas"
           style={{ position: "relative", width: 400, height: 300 }}
           aria-label="Physics particle diagram">

        {/* Force field background would go here */}

        {/* Particles */}
        {particleNodes.map((particle, i) => (
          <g
            key={particle.id}
            style={{
              position: "absolute",
              left: 200 + particle.x,
              top: 150 - particle.y,
              width: 12 + particle.mass * 2,
              height: 12 + particle.mass * 2,
            }}
          >
            <circle
              fill="var(--primary)"
              stroke="var(--foreground)"
              strokeWidth={1}
              r={6 + particle.mass}
            />
            <text
              x="50%"
              y="50%"
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={10}
              fill="var(--card-foreground)"
            >
              {particle.id}
            </text>
          </g>
        ))}

        {/* Velocity vectors (reduced motion aware) */}
        {!reducedMotion && particleNodes.slice(0, 3).map((particle, i) => (
          <g
            key={`vel-${particle.id}`}
            style={{
              position: "absolute",
              left: 200 + particle.x,
              top: 150 - particle.y,
            }}
          >
            <line
              x1={0}
              y1={0}
              x2={20}
              y2={-10}
              stroke="var(--primary)"
              strokeWidth={1}
              strokeDasharray="4 2"
            />
            <text
              x={25}
              y={-5}
              fontSize={10}
              fill="var(--foreground)"
            >
              {particle.vx > 0 ? "→" : "←"}
            </text>
          </g>
        ))}
      </div>

      <div className="physics-details mt-2">
        <div className="detail-row">
          <span className="detail-label">Particles</span>
          <span className="detail-value">{particles.length}</span>
        </div>
        {state?.currentParticleId && (
          <div className="detail-row">
            <span className="detail-label">Current</span>
            <span className="detail-value">{state.currentParticleId}</span>
          </div>
        )}
      </div>
    </div>
  );
}