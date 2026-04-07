"""Test for RockerBogie configuration fix"""
from unittest.mock import patch, MagicMock
from Config import get_config


def test_rocker_bogie_config():
    """Test that RockerBogie correctly loads configuration"""
    # Mock the servo hat to avoid hardware dependency
    with patch('RockerBogie.get_servo_hat') as mock_servo_hat:
        mock_servo_hat.return_value = MagicMock()
        
        from RockerBogie import RockerBogie
        
        # Create an instance
        rocker_bogie = RockerBogie()
        
        # Verify configuration was loaded correctly
        config = get_config()["rocker_bogie"]
        
        assert rocker_bogie.channels == config["channels"], \
            f"Channels mismatch: {rocker_bogie.channels} != {config['channels']}"
        
        assert rocker_bogie.sun_position == config["sun_position"], \
            f"Sun position mismatch: {rocker_bogie.sun_position} != {config['sun_position']}"
        
        assert rocker_bogie.regular_position == config["regular_position"], \
            f"Regular position mismatch: {rocker_bogie.regular_position} != {config['regular_position']}"
        
        print("✓ RockerBogie configuration loaded correctly")
        print(f"  Channels: {rocker_bogie.channels}")
        print(f"  Sun position: {rocker_bogie.sun_position}")
        print(f"  Regular position: {rocker_bogie.regular_position}")
        print("\n✓ All tests passed!")


if __name__ == "__main__":
    test_rocker_bogie_config()
