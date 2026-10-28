/**
 * Image Panel Visualization Component
 *
 * Renders an image with optional caption.
 */

"use client";

import { useRef, useState, useEffect } from "react";

/** Image panel data specification */
export interface ImagePanelData {
  image_url: string;
  caption?: string;
}

/** Image panel visualization props */
export interface ImagePanelVisualizationProps {
  data: ImagePanelData;
  controls?: any;
  state?: any;
  reducedMotion?: boolean;
}

/** Image Panel Visualization Component */
export function ImagePanelVisualization({
  data,
  controls,
  state,
  reducedMotion,
}: ImagePanelVisualizationProps) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [mounted, setMounted] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return <div className="image-placeholder">Loading image...</div>;
  }

  return (
    <div className="image-panel-visualization" aria-label="Image panel">
      <div className="image-panel-header">
        <span className="image-panel-title">Image</span>
      </div>

      <div className="image-panel-container"
           style={{ position: "relative", width: "100%", maxWidth: 600, height: 0, paddingBottom: "56.25%" }}
           aria-label="Image display">
        {imageError ? (
          <div className="image-error" style={{
            position: "absolute",
            inset: 0,
            background: "var(--card)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--muted-foreground)"
          }}>
            Failed to load image
          </div>
        ) : imageLoaded ? (
          <img
            ref={imgRef}
            src={data.image_url}
            alt={data.caption || ""}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "contain",
            }}
            onError={() => setImageError(true)}
            loading="lazy"
          />
        ) : (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "var(--card)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--muted-foreground)"
            }}
          >
            <span>Loading image...</span>
          </div>
        )}
      </div>

      {data.caption && (
        <div className="image-panel-caption" style={{ padding: "12px", borderTop: "1px solid var(--border)", marginTop: "8px" }}>
          <span className="image-panel-caption-text">{data.caption}</span>
        </div>
      )}
    </div>
  );
}