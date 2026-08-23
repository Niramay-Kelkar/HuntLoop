import { redirect } from "next/navigation";

// The mockup treats Dashboard as the app's home view (clicking the logo
// navigates there - see design/HuntLoop.dc.html's goHome). The real Jobs
// list now lives at its own /jobs route (see frontend/src/app/jobs/page.tsx)
// instead of at "/" - fixes the nav links that pointed at /jobs while
// nothing was mounted there.
export default function RootPage() {
  redirect("/dashboard");
}
