// clearSaveData.js

const STORAGE_KEY = 'dopPortalState';

/**
 * Clears all saved information for this application.
 */
export function clearSavedData() {
    const confirmed = confirm(
        "⚠️ Clear all saved information?\n\n" +
        "This will remove the saved table data, database configuration, " +
        "page position, and other saved session information.\n\n" +
        "This action cannot be undone."
    );

    if (!confirmed) {
        return;
    }

    // 1. Remove saved state from LocalStorage
    localStorage.removeItem(STORAGE_KEY);

    // 2. Clear state in memory and re-render UI safely via global handler
    if (typeof window.resetPortalState === 'function') {
        window.resetPortalState();
    }

    alert("✅ All saved information has been cleared.");
}