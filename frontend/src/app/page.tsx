import { AppShell } from "@/components/app-shell";
import { RobotProvider } from "@/components/robot/robot-context";

export default function Home() {
  return (
    <RobotProvider>
      <AppShell />
    </RobotProvider>
  );
}
