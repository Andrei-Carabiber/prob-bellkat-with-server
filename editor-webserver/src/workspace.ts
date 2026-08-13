import path from 'node:path';
import fs from 'node:fs/promises';

const TEMPLATE_DIR = path.resolve('/opt/pbkat');

export const SHARED_BUILD_DIR = path.join(TEMPLATE_DIR, 'shared-build-cache');

export async function createIsolatedWorkspace(id: string): Promise<string> {
    const workspacePath = path.resolve(`/tmp/pbkat-workspace-${id}`);
    await fs.mkdir(workspacePath, { recursive: true });

    try {
        const entries = await fs.readdir(TEMPLATE_DIR, { withFileTypes: true });
        for (const entry of entries) {
            if (
                entry.name === 'dist-newstyle' ||
                entry.name === '.git' ||
                entry.name === 'shared-build-cache'
            ) {
                continue;
            }
            const srcPath = path.join(TEMPLATE_DIR, entry.name);
            const destPath = path.join(workspacePath, entry.name);
            await fs.cp(srcPath, destPath, { recursive: true });
        }

        await fs.cp(SHARED_BUILD_DIR, path.join(workspacePath, 'dist-newstyle'), { recursive: true });

        return workspacePath;
    } catch (err) {
        await fs.rm(workspacePath, { recursive: true, force: true }).catch(() => {});
        throw err;
    }
}