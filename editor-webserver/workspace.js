import path from 'node:path';
import fs from 'node:fs/promises';

export const TEMPLATE_DIR = path.resolve('/opt/pbkat');

export async function createIsolatedWorkspace(id) {
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
        return workspacePath;
    } catch (err) {
        await fs.rm(workspacePath, { recursive: true, force: true }).catch(() => {});
        throw err;
    }
}
