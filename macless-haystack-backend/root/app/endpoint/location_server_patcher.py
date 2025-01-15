import sqlite3
from dataclasses import dataclass
from typing import Callable

from location_server_helper import db_path, logger


@dataclass
class Patch:
    name: str
    commands: list[str | Callable]


def apply_patches(db_path, patches: list[Patch]):
    """
    Applies necessary patches to the database.
    """
    try:
        # Connect to the database
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        # Step 1: Create the 'schema_patches' table if it doesn't exist
        c.execute(
            """
        CREATE TABLE IF NOT EXISTS schema_patches (
            patch_name TEXT PRIMARY KEY,
            status INTEGER NOT NULL
        )
        """
        )
        conn.commit()
        logger.info("Checked for 'schema_patches' table and created if not exists.")

        # Step 2: Iterate over the patches
        for patch in patches:
            patch_name = patch.name
            c.execute(
                "SELECT status FROM schema_patches WHERE patch_name = ?", (patch_name,)
            )
            result = c.fetchone()

            if result is None or result[0] == 0:
                logger.info(
                    f"Patch '{patch_name}' has not been applied yet. Applying patch..."
                )

                try:
                    # Begin a transaction for this patch
                    conn.execute("BEGIN")

                    # Execute each command in the patch
                    for command in patch.commands:
                        logger.debug(f"Executing command: {command}")
                        # Special handling if command requires parameters
                        if callable(command):
                            # If the command is a function, call it with cursor and connection
                            command(c, conn)
                        else:
                            c.execute(command)

                    # Mark the patch as applied
                    c.execute(
                        "INSERT OR REPLACE INTO schema_patches(patch_name, status) VALUES (?, ?)",
                        (patch_name, 1),
                    )
                    conn.commit()
                    logger.info(f"Patch '{patch_name}' has been applied and recorded.")

                except sqlite3.Error as e:
                    conn.rollback()
                    logger.error(
                        f"An error occurred while applying patch '{patch_name}': {e}"
                    )
                    raise e  # Re-raise the exception to handle it outside if needed
            else:
                logger.info(
                    f"Patch '{patch_name}' has already been applied. Skipping patch."
                )

    except sqlite3.Error as e:
        logger.error(f"An error occurred while applying patches: {e}")
    finally:
        # Close the database connection
        if conn:
            conn.close()
            logger.info("Database connection closed.")


# Helper function for the specific patch logic
def apply_rowcount_fix(cursor, conn):
    """
    Applies the rowcount fix by counting rows in 'reports' and updating 'rowcount' table.
    """
    # Calculate total rows in 'reports'
    cursor.execute("SELECT COUNT(*) FROM reports")
    total_rows = cursor.fetchone()[0]
    logger.info(f"Total number of rows in 'reports': {total_rows}")

    # Ensure the 'rowcount' table exists
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS rowcount (
        count INTEGER
    )
    """
    )
    conn.commit()

    # Clear existing data in 'rowcount' table
    cursor.execute("DELETE FROM rowcount")
    conn.commit()

    # Insert the new rowcount value
    cursor.execute("INSERT INTO rowcount(count) VALUES (?)", (total_rows,))
    conn.commit()
    logger.info(f"Inserted total_rows '{total_rows}' into 'rowcount' table.")


# Example usage
if __name__ == "__main__":
    # Define the patches
    patches = [
        Patch(
            name="rowcount_fix_2025_01_15_62b664af_17470c17",
            commands=[
                apply_rowcount_fix  # This is a function that performs the necessary operations
            ],
        ),
    ]

    # Apply the patches
    apply_patches(db_path, patches)
