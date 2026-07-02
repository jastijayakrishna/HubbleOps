class DropLegacyUsers < ActiveRecord::Migration[7.1]
  def change
    drop_table :legacy_users
  end
end
