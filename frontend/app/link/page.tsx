import { Suspense } from 'react';
import { LinkViewer } from './link-viewer';

export default function LinkPage() {
  return (
    <Suspense fallback={<div className="h-screen bg-white" />}>
      <LinkViewer />
    </Suspense>
  );
}

