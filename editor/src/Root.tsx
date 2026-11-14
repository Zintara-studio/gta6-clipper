import React from "react";
import { Composition } from "remotion";
import { Short, defaultProps, type ShortProps } from "./Short";

export const Root: React.FC = () => (
  <Composition
    id="Short"
    component={Short}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={defaultProps}
    calculateMetadata={({ props }: { props: ShortProps }) => ({
      fps: props.fps,
      durationInFrames: Math.max(1, Math.round(props.duration * props.fps)),
    })}
  />
);
